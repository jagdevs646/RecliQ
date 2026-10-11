from __future__ import annotations

from urllib.parse import urlsplit

from app.core.config import Settings, get_settings


LOCAL_ORIGINS = (
    "http://localhost:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
)


def _normalize_origin(value: str) -> str | None:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def allowed_origins(settings: Settings | None = None) -> list[str]:
    settings = settings or get_settings()
    candidates = [settings.frontend_origin, *settings.extra_cors_origins.split(","), *LOCAL_ORIGINS]
    if settings.environment.lower() == "test":
        candidates.append("http://testserver")
    return list(dict.fromkeys(origin for item in candidates if (origin := _normalize_origin(item))))


def is_allowed_origin(origin: str | None, settings: Settings | None = None) -> bool:
    normalized = _normalize_origin(origin or "")
    return normalized is not None and normalized in allowed_origins(settings)
