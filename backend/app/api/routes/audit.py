import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_actor, get_session_id
from app.database.session import get_db
from app.models.account import WorkspaceImport
from app.models.audit import AuditEvent
from app.services.audit_service import (
    AuditActor,
    event_to_dict,
    export_csv,
    list_events,
    record_event,
    verify_chain,
)

router = APIRouter(prefix="/audit", tags=["audit"])


def _accessible_scopes(db: Session, current_scope: str) -> list[str]:
    """Include source chains explicitly imported into the current account."""
    sources = db.scalars(
        select(WorkspaceImport.source_scope).where(WorkspaceImport.target_scope == current_scope)
    ).all()
    return list(dict.fromkeys([current_scope, *sources]))


def _list_scoped_events(
    db: Session,
    scopes: list[str],
    *,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[AuditEvent]:
    query = select(AuditEvent).where(AuditEvent.scope_id.in_(scopes))
    if action:
        query = query.where(AuditEvent.action == action)
    if entity_type:
        query = query.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditEvent.entity_id == entity_id)
    query = query.order_by(AuditEvent.id.desc()).offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return list(db.scalars(query))


def _verify_scopes(db: Session, scopes: list[str]) -> dict:
    chains = [{"scope_id": scope, **verify_chain(db, scope)} for scope in scopes]
    if len(chains) == 1:
        return {key: value for key, value in chains[0].items() if key != "scope_id"}
    return {
        "valid": all(chain["valid"] for chain in chains),
        "checked": sum(chain["checked"] for chain in chains),
        "broken_at": next((chain["broken_at"] for chain in chains if not chain["valid"]), None),
        "reason": next((chain["reason"] for chain in chains if not chain["valid"]), ""),
        "chains": chains,
    }


@router.get("/events")
def audit_events(
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> dict:
    scopes = _accessible_scopes(db, session_id)
    events = _list_scoped_events(db, scopes, action=action, entity_type=entity_type, entity_id=entity_id, limit=limit, offset=offset)
    return {"events": [event_to_dict(event) for event in events], "limit": limit, "offset": offset}


@router.get("/verify")
def verify_audit_chain(db: Session = Depends(get_db), session_id: str = Depends(get_session_id)) -> dict:
    """Recompute the hash chain: any edited or removed event is reported."""
    return _verify_scopes(db, _accessible_scopes(db, session_id))


@router.get("/export")
def export_audit_log(
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    entity_id: str | None = None,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> Response:
    scopes = _accessible_scopes(db, session_id)
    events = list(reversed(_list_scoped_events(db, scopes, entity_id=entity_id)))  # Oldest first.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    record_event(
        scope_id=session_id, actor=actor, action="audit.exported", entity_type="audit_log",
        summary=f"Exported {len(events)} audit event(s) as {format.upper()}", metadata={"entity_id": entity_id},
    )
    if format == "json":
        body = json.dumps(
            {"exported_at": stamp, "verification": _verify_scopes(db, scopes), "events": [event_to_dict(event) for event in events]},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        return Response(body, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="RecliQ_Audit_Log_{stamp}.json"'})
    return Response(
        "﻿" + export_csv(events),  # BOM so Excel opens UTF-8 correctly.
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="RecliQ_Audit_Log_{stamp}.csv"'},
    )
