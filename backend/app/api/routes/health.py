from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.database.session import engine


router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "RecliQ API"}


@router.get("/health/ready")
def readiness() -> JSONResponse:
    """Dependencies needed to accept work: the database and (if used) Redis."""
    checks: dict[str, str] = {}
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover - depends on infrastructure
        checks["database"] = f"error: {type(exc).__name__}"
    if get_settings().job_queue_backend.lower() == "redis":
        try:
            from app.jobs.queue import redis_connection

            redis_connection().ping()
            checks["queue"] = "ok"
        except Exception as exc:  # pragma: no cover
            checks["queue"] = f"error: {type(exc).__name__}"
    else:
        checks["queue"] = "in-process"
    healthy = all(value in {"ok", "in-process"} for value in checks.values())
    return JSONResponse({"status": "ok" if healthy else "degraded", "checks": checks}, status_code=200 if healthy else 503)
