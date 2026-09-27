"""Append-only, hash-chained audit log.

Every important action (uploads, runs, cancellations, deletions, template and
alias changes, review decisions, downloads) is recorded with who did it, when,
from where, and the before/after values. Each event carries the hash of the
previous event in the same scope, so a modified or removed event is detected
by ``verify_chain``.

Actor identity: RecliQ has no user accounts yet, so the actor is the
anonymous browser session (``actor_type="session"``) or a system component
(``actor_type="system"``). The model already separates actor and scope so a
signed-in user and their organization slot in without a schema change.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.observability import capture_exception, request_id_var
from app.database.session import SessionLocal
from app.models.audit import AuditEvent
from app.models.base import new_id
from app.utils.json_encoder import safe_json_dumps

GENESIS_HASH = "0" * 64
_chain_lock = threading.Lock()
logger = logging.getLogger("recliq.audit")

EXPORT_COLUMNS = (
    "sequence", "event_id", "occurred_at", "actor_type", "actor_id", "ip_address", "user_agent",
    "request_id", "action", "entity_type", "entity_id", "summary", "before", "after", "metadata",
    "prev_hash", "hash",
)


@dataclass(frozen=True)
class AuditActor:
    actor_type: str
    actor_id: str
    ip_address: str | None = None
    user_agent: str | None = None

    @classmethod
    def from_request(cls, request: Any, session_id: str) -> "AuditActor":
        client = getattr(request, "client", None)
        headers = getattr(request, "headers", {}) or {}
        forwarded = headers.get("x-forwarded-for", "") if hasattr(headers, "get") else ""
        ip = forwarded.split(",")[0].strip() if forwarded else (client.host if client else None)
        agent = headers.get("user-agent") if hasattr(headers, "get") else None
        return cls("session", session_id, ip, (agent or "")[:300] or None)

    @classmethod
    def system(cls, component: str = "worker") -> "AuditActor":
        return cls("system", component)


def _utc_naive(value: datetime) -> datetime:
    """SQLite drops time zones; hash a UTC value that round-trips identically."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _event_hash(event: AuditEvent) -> str:
    payload = {
        "event_id": event.event_id,
        "occurred_at": _utc_naive(event.occurred_at).isoformat(timespec="microseconds"),
        "scope_id": event.scope_id,
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "ip_address": event.ip_address,
        "user_agent": event.user_agent,
        "request_id": event.request_id,
        "action": event.action,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "summary": event.summary,
        "before": event.before_json,
        "after": event.after_json,
        "metadata": event.metadata_json,
        "prev_hash": event.prev_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _dump(value: Any) -> str | None:
    return None if value is None else safe_json_dumps(value)


def record_event(
    *,
    scope_id: str,
    actor: AuditActor,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    summary: str = "",
    before: Any = None,
    after: Any = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent | None:
    """Append one event in its own transaction.

    The chain lock (plus a PostgreSQL advisory lock across processes) is held
    until commit, so events in a scope form one unbroken sequence. A failure
    to write is logged and reported, never raised into the business action.
    """
    db = SessionLocal()
    try:
        with _chain_lock:
            if db.get_bind().dialect.name == "postgresql":
                db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:scope))"), {"scope": f"audit:{scope_id}"})
            previous = db.scalar(
                select(AuditEvent.hash).where(AuditEvent.scope_id == scope_id).order_by(AuditEvent.id.desc()).limit(1)
            )
            event = AuditEvent(
                event_id=new_id(),
                occurred_at=_utc_naive(datetime.now(timezone.utc)),
                scope_id=scope_id,
                actor_type=actor.actor_type,
                actor_id=actor.actor_id,
                ip_address=actor.ip_address,
                user_agent=actor.user_agent,
                request_id=request_id_var.get(),
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                summary=summary,
                before_json=_dump(before),
                after_json=_dump(after),
                metadata_json=safe_json_dumps(metadata or {}),
                prev_hash=previous or GENESIS_HASH,
            )
            event.hash = _event_hash(event)
            db.add(event)
            db.commit()
            db.refresh(event)
            db.expunge(event)
        logger.info("audit %s", action, extra={"audit_action": action, "entity_type": entity_type, "entity_id": entity_id})
        return event
    except Exception as exc:  # pragma: no cover - exercised by failure injection only
        db.rollback()
        capture_exception(exc, audit_action=action, entity_id=entity_id)
        return None
    finally:
        db.close()


def list_events(
    db: Session,
    scope_id: str,
    *,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[AuditEvent]:
    query = select(AuditEvent).where(AuditEvent.scope_id == scope_id)
    if action:
        query = query.where(AuditEvent.action == action)
    if entity_type:
        query = query.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditEvent.entity_id == entity_id)
    query = query.order_by(AuditEvent.id.desc()).offset(offset)
    if limit:
        query = query.limit(limit)
    return list(db.scalars(query))


def event_to_dict(event: AuditEvent) -> dict[str, Any]:
    def load(value: str | None) -> Any:
        if value is None:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    return {
        "sequence": event.id,
        "event_id": event.event_id,
        "occurred_at": _utc_naive(event.occurred_at).isoformat() + "Z",
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "ip_address": event.ip_address,
        "user_agent": event.user_agent,
        "request_id": event.request_id,
        "action": event.action,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "summary": event.summary,
        "before": load(event.before_json),
        "after": load(event.after_json),
        "metadata": load(event.metadata_json),
        "prev_hash": event.prev_hash,
        "hash": event.hash,
    }


def verify_chain(db: Session, scope_id: str) -> dict[str, Any]:
    """Recompute every hash in order; report the first break, if any."""
    expected_previous = GENESIS_HASH
    checked = 0
    for event in db.scalars(select(AuditEvent).where(AuditEvent.scope_id == scope_id).order_by(AuditEvent.id)):
        if event.prev_hash != expected_previous:
            return {"valid": False, "checked": checked, "broken_at": event.event_id, "reason": "An earlier event is missing or was altered."}
        if _event_hash(event) != event.hash:
            return {"valid": False, "checked": checked, "broken_at": event.event_id, "reason": "This event was modified after it was recorded."}
        expected_previous = event.hash
        checked += 1
    return {"valid": True, "checked": checked, "broken_at": None, "reason": ""}


def export_csv(events: Iterable[AuditEvent]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS)
    writer.writeheader()
    for event in events:
        row = event_to_dict(event)
        for key in ("before", "after", "metadata"):
            row[key] = "" if row[key] is None else json.dumps(row[key], ensure_ascii=False, default=str)
        writer.writerow(row)
    return buffer.getvalue()
