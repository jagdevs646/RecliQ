from fastapi import Depends, Request

from app.services.audit_service import AuditActor


def get_session_id(request: Request) -> str:
    session_id = getattr(request.state, "session_id", None)
    if not session_id:
        raise RuntimeError("Anonymous session middleware is not configured")
    return session_id


def get_actor(request: Request, session_id: str = Depends(get_session_id)) -> AuditActor:
    """Who is acting, for the audit log (the browser session until sign-in exists)."""
    return AuditActor.from_request(request, session_id)
