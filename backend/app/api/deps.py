from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.account import User
from app.services.audit_service import AuditActor


def get_session_id(request: Request) -> str:
    workspace_id = getattr(request.state, "workspace_id", None)
    if getattr(request.state, "auth_blocked", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is not permitted to access the workspace.")
    if not workspace_id:
        raise RuntimeError("Anonymous session middleware is not configured")
    return workspace_id


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue.")
    user = db.get(User, user_id)
    if not user or user.status not in {"active", "restricted"}:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session is no longer valid.")
    return user


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    if getattr(request.state, "auth_blocked", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is not permitted to access the workspace.")
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return None
    user = db.get(User, user_id)
    if not user or user.status not in {"active", "restricted"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is not permitted to access the workspace.")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "super_admin" or user.status != "active" or not user.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super Admin access is required.")
    return user


def get_actor(request: Request, session_id: str = Depends(get_session_id)) -> AuditActor:
    """The authenticated user or anonymous guest performing the action."""
    actor = AuditActor.from_request(request, session_id)
    user_id = getattr(request.state, "user_id", None)
    return AuditActor("user", user_id, actor.ip_address, actor.user_agent) if user_id else actor
