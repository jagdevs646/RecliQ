from fastapi import Request


def get_session_id(request: Request) -> str:
    session_id = getattr(request.state, "session_id", None)
    if not session_id:
        raise RuntimeError("Anonymous session middleware is not configured")
    return session_id
