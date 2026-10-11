from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pyotp
from fastapi import HTTPException, Request, status
from pwdlib import PasswordHash
from sqlalchemy import case, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.account import AdminAuditEvent, AuthRateLimit, User, UserSession
from app.models.base import new_id
from app.utils.json_encoder import safe_json_dumps


password_hasher = PasswordHash.recommended()
_admin_audit_lock = threading.Lock()
logger = logging.getLogger("recliq.auth")


@dataclass(frozen=True)
class AuthSessionContext:
    auth_session_id: str
    mfa_verified: bool
    user_id: str
    user_role: str
    user_status: str
    user_mfa_enabled: bool


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return password_hasher.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(48)


def csrf_for_token(token: str) -> str:
    key = get_settings().effective_auth_csrf_secret.encode("utf-8")
    if not key:
        return ""
    return hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()


def validate_csrf(token: str, provided: str | None) -> bool:
    expected = csrf_for_token(token)
    return bool(expected and provided and hmac.compare_digest(expected, provided))


def create_login_session(db: Session, user: User, request: Request, *, mfa_verified: bool) -> tuple[str, UserSession]:
    token = new_token()
    now = datetime.now(timezone.utc)
    session = UserSession(
        user_id=user.id,
        token_hash=token_digest(token),
        created_at=now,
        expires_at=now + timedelta(days=get_settings().auth_session_days),
        last_seen_at=now,
        ip_address=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:300] or None,
        mfa_verified=mfa_verified,
    )
    db.add(session)
    user.last_login_at = now
    user.last_seen_at = now
    db.flush()
    return token, session


def lookup_auth_session(token: str | None) -> AuthSessionContext | None:
    if not token:
        return None
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        found = db.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(UserSession.token_hash == token_digest(token), UserSession.revoked_at.is_(None), UserSession.expires_at > now)
        ).first()
        if not found:
            return None
        session, user = found
        context = AuthSessionContext(
            auth_session_id=session.id,
            mfa_verified=session.mfa_verified,
            user_id=user.id,
            user_role=user.role,
            user_status=user.status,
            user_mfa_enabled=user.mfa_enabled,
        )
        seen_at = session.last_seen_at
        if seen_at.tzinfo is None:
            seen_at = seen_at.replace(tzinfo=timezone.utc)
        if now - seen_at >= timedelta(minutes=5):
            session.last_seen_at = now
            user.last_seen_at = now
            db.commit()
        return context
    finally:
        db.close()


def verify_totp(user: User, code: str | None) -> bool:
    if not user.mfa_secret or not code:
        return False
    return pyotp.TOTP(user.mfa_secret).verify(code.strip(), valid_window=1)


def _rate_fingerprint(request: Request, action: str, identity: str | None) -> str:
    ip = request.client.host if request.client else "unknown"
    scope = f"{action.strip().lower()}:{ip.lower()}:{(identity or '').strip().casefold()}"
    return token_digest(scope)


def _rate_limit_error() -> HTTPException:
    settings = get_settings()
    return HTTPException(
        status_code=429,
        detail="Too many attempts. Wait before trying again.",
        headers={"Retry-After": str(settings.auth_attempt_window_minutes * 60)},
    )


def _increment_rate_counter(db: Session, fingerprint: str, now: datetime, cutoff: datetime) -> int:
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        insert = pg_insert
    elif dialect == "sqlite":
        insert = sqlite_insert
    else:  # pragma: no cover - production and test databases are PostgreSQL/SQLite.
        raise RuntimeError(f"Atomic auth throttling is unsupported for database dialect {dialect!r}.")

    statement = insert(AuthRateLimit).values(
        fingerprint=fingerprint,
        window_started_at=now,
        attempts=1,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[AuthRateLimit.fingerprint],
        set_={
            "window_started_at": case(
                (AuthRateLimit.window_started_at < cutoff, now),
                else_=AuthRateLimit.window_started_at,
            ),
            "attempts": case(
                (AuthRateLimit.window_started_at < cutoff, 1),
                else_=AuthRateLimit.attempts + 1,
            ),
        },
    ).returning(AuthRateLimit.attempts)
    return int(db.scalar(statement) or 0)


def record_auth_attempt(
    db: Session,
    request: Request,
    identity: str | None,
    *,
    action: str,
) -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=settings.auth_attempt_window_minutes)
    fingerprint = _rate_fingerprint(request, action, identity)
    attempts = _increment_rate_counter(db, fingerprint, now, cutoff)
    db.commit()
    if attempts >= settings.auth_max_attempts:
        raise _rate_limit_error()


def record_auth_failure(db: Session, request: Request, identity: str, *, action: str = "login") -> None:
    record_auth_attempt(db, request, identity, action=action)


def check_auth_rate_limit(
    db: Session,
    request: Request,
    identity: str | None,
    *,
    action: str = "login",
) -> None:
    settings = get_settings()
    row = db.get(AuthRateLimit, _rate_fingerprint(request, action, identity))
    now = datetime.now(timezone.utc)
    if row and _aware(row.window_started_at) >= now - timedelta(minutes=settings.auth_attempt_window_minutes) and row.attempts >= settings.auth_max_attempts:
        retry = max(1, int((_aware(row.window_started_at) + timedelta(minutes=settings.auth_attempt_window_minutes) - now).total_seconds()))
        raise HTTPException(status_code=429, detail="Too many attempts. Wait before trying again.", headers={"Retry-After": str(retry)})


def clear_auth_failures(db: Session, request: Request, identity: str, *, action: str = "login") -> None:
    db.query(AuthRateLimit).filter(
        AuthRateLimit.fingerprint == _rate_fingerprint(request, action, identity)
    ).delete(synchronize_session=False)
    db.commit()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def record_admin_event(
    db: Session,
    request: Request,
    *,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str | None = None,
    before: Any = None,
    after: Any = None,
    metadata: dict[str, Any] | None = None,
    result: str = "success",
) -> AdminAuditEvent:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with _admin_audit_lock:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:scope))"), {"scope": "admin-audit"})
        prev_hash = db.scalar(select(AdminAuditEvent.hash).order_by(AdminAuditEvent.id.desc()).limit(1)) or ("0" * 64)
        event = AdminAuditEvent(
            event_id=new_id(), occurred_at=now, actor_id=actor_id, action=action,
            target_type=target_type, target_id=target_id, result=result,
            ip_address=request.client.host if request.client else None,
            request_id=request.headers.get("X-Request-ID"), before_json=safe_json_dumps(before) if before is not None else None,
            after_json=safe_json_dumps(after) if after is not None else None,
            metadata_json=safe_json_dumps(metadata or {}), prev_hash=prev_hash, hash="",
        )
        content = {
            "event_id": event.event_id,
            "occurred_at": now.isoformat(timespec="microseconds"),
            "actor_id": event.actor_id,
            "action": event.action,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "result": event.result,
            "ip_address": event.ip_address,
            "request_id": event.request_id,
            "before": event.before_json,
            "after": event.after_json,
            "metadata": event.metadata_json,
            "prev_hash": event.prev_hash,
        }
        event.hash = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        db.add(event)
        db.flush()
        return event


def public_user(user: User, *, theme: str = "system") -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "status": user.status,
        "email_verified": user.email_verified,
        "mfa_enabled": user.mfa_enabled,
        "has_password": user.password_hash is not None,
        "theme": theme,
    }
