from __future__ import annotations

from uuid import UUID, uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
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
        request.state.workspace_id = session_id
        request.state.user_id = None
        request.state.user_role = None
        request.state.auth_session_id = None
        request.state.auth_blocked = False
        auth_token = request.cookies.get(get_settings().auth_cookie_name)
        auth_context = None
        if auth_token:
            from app.services.account_security import lookup_auth_session

            auth_context = lookup_auth_session(auth_token)
        if auth_context:
            if auth_context.user_status in {"suspended", "deleted"} or (
                auth_context.user_role == "super_admin"
                and (not auth_context.user_mfa_enabled or not auth_context.mfa_verified)
            ):
                request.state.auth_blocked = True
            else:
                request.state.user_id = auth_context.user_id
                request.state.user_role = auth_context.user_role
                request.state.auth_session_id = auth_context.auth_session_id
                request.state.workspace_id = f"user-{auth_context.user_id}"
        if (
            request.state.user_id
            and request.method not in {"GET", "HEAD", "OPTIONS", "TRACE"}
            and not self._csrf_valid(auth_token, request.headers.get("X-CSRF-Token"))
        ):
            return JSONResponse({"detail": "Your security token expired. Refresh and try again."}, status_code=403)
        response = await call_next(request)
        response.headers[SESSION_HEADER_NAME] = session_id
        if request.state.user_id and auth_token:
            from app.services.account_security import csrf_for_token

            response.headers["X-CSRF-Token"] = csrf_for_token(auth_token)
        if created:
            secure = get_settings().session_cookie_secure
            # SameSite=None is required for cross-origin requests (Vercel → Render).
            # It is only valid when Secure=True, which is enforced in production.
            samesite = "none" if secure else "lax"
            response.set_cookie(
                SESSION_COOKIE_NAME,
                session_id,
                max_age=SESSION_COOKIE_MAX_AGE,
                httponly=True,
                samesite=samesite,
                secure=secure,
                path="/",
            )
        return response

    @staticmethod
    def _csrf_valid(token: str | None, provided: str | None) -> bool:
        from app.services.account_security import validate_csrf

        return validate_csrf(token or "", provided)


def websocket_session_id(websocket: WebSocket) -> str:
    session_id, _ = resolve_session_id(
        websocket.cookies.get(SESSION_COOKIE_NAME),
        websocket.headers.get(SESSION_HEADER_NAME),
    )
    token = websocket.cookies.get(get_settings().auth_cookie_name)
    if token:
        from app.services.account_security import lookup_auth_session

        auth_context = lookup_auth_session(token)
        if auth_context:
            if auth_context.user_status == "active" and (
                auth_context.user_role != "super_admin"
                or (auth_context.user_mfa_enabled and auth_context.mfa_verified)
            ):
                return f"user-{auth_context.user_id}"
    return session_id
