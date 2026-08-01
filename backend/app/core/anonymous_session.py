from __future__ import annotations

from uuid import UUID, uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.websockets import WebSocket

from app.core.config import get_settings


SESSION_COOKIE_NAME = "recliq_session"
SESSION_HEADER_NAME = "X-Session-ID"
SESSION_COOKIE_MAX_AGE = 60 * 60 * 24


def generate_session_id() -> str:
    return str(uuid4())


def validate_session_id(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return None
    return str(parsed) if parsed.version == 4 else None


def resolve_session_id(cookie_value: str | None, header_value: str | None) -> tuple[str, bool]:
    """Return a valid session and whether a new session had to be created."""
    session_id = validate_session_id(cookie_value) or validate_session_id(header_value)
    if session_id:
        return session_id, False
    return generate_session_id(), True


class AnonymousSessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        session_id, created = resolve_session_id(
            request.cookies.get(SESSION_COOKIE_NAME),
            request.headers.get(SESSION_HEADER_NAME),
        )
        request.state.session_id = session_id
        response = await call_next(request)
        response.headers[SESSION_HEADER_NAME] = session_id
        if created:
            response.set_cookie(
                SESSION_COOKIE_NAME,
                session_id,
                max_age=SESSION_COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
                secure=get_settings().session_cookie_secure,
                path="/",
            )
        return response


def websocket_session_id(websocket: WebSocket) -> str:
    session_id, _ = resolve_session_id(
        websocket.cookies.get(SESSION_COOKIE_NAME),
        websocket.headers.get(SESSION_HEADER_NAME),
    )
    return session_id
