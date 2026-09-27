"""Structured logging, request/job correlation and error tracking.

Every log line is one JSON object carrying the current ``request_id``,
``job_id`` and ``session_id`` (from context variables), so a single request or
reconciliation job can be followed across the API and the workers. Errors are
also sent to Sentry when ``SENTRY_DSN`` is configured.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

REQUEST_ID_HEADER = "X-Request-ID"

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)
session_id_var: ContextVar[str | None] = ContextVar("session_id", default=None)

_CONTEXT_VARS = {"request_id": request_id_var, "job_id": job_id_var, "session_id": session_id_var}
# Attributes every LogRecord has; anything else was passed via ``extra=``.
_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_sentry_enabled = False

logger = logging.getLogger("recliq")


def current_context() -> dict[str, str]:
    return {name: value for name, var in _CONTEXT_VARS.items() if (value := var.get())}


@contextmanager
def bind_context(**values: str | None) -> Iterator[None]:
    """Attach request/job/session identifiers to every log line in the block."""
    tokens = [(_CONTEXT_VARS[name], _CONTEXT_VARS[name].set(value)) for name, value in values.items() if name in _CONTEXT_VARS]
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **current_context(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["error_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["stack"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        context = " ".join(f"{key}={value}" for key, value in current_context().items())
        base = super().format(record)
        return f"{base} [{context}]" if context else base


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    # Replace handlers we installed before; keep pytest's capture handlers.
    for existing in list(root.handlers):
        if getattr(existing, "_recliq", False):
            root.removeHandler(existing)
    handler._recliq = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level.upper())
    # Uvicorn's own access log duplicates the structured request log below.
    logging.getLogger("uvicorn.access").propagate = False
    logging.getLogger("uvicorn.access").handlers = []


def init_error_tracking(dsn: str | None, environment: str, release: str | None, traces_sample_rate: float = 0.0) -> bool:
    """Enable Sentry when a DSN is configured. Returns whether it is active."""
    global _sentry_enabled
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover - optional dependency
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed")
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        traces_sample_rate=traces_sample_rate,
        send_default_pii=False,  # Uploaded financial data must not leave the system.
    )
    _sentry_enabled = True
    return True


def capture_exception(exc: BaseException, **context: Any) -> None:
    """Log an exception with its context and forward it to error tracking."""
    logger.error("Unhandled error: %s", exc, exc_info=(type(exc), exc, exc.__traceback__), extra=context)
    if not _sentry_enabled:
        return
    import sentry_sdk

    with sentry_sdk.isolation_scope() as scope:
        for key, value in {**current_context(), **context}.items():
            scope.set_tag(key, str(value)[:200])
        sentry_sdk.capture_exception(exc)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request ID, log one structured line per request, and turn
    unhandled errors into a JSON 500 that carries the request ID."""

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid4().hex
        session_id = getattr(request.state, "session_id", None)
        started = time.perf_counter()
        with bind_context(request_id=request_id, session_id=session_id):
            try:
                response = await call_next(request)
            except Exception as exc:
                capture_exception(exc, path=request.url.path, method=request.method)
                response = JSONResponse({"detail": "Internal server error", "request_id": request_id}, status_code=500)
            response.headers[REQUEST_ID_HEADER] = request_id
            if request.url.path != "/health":
                logger.info(
                    "%s %s -> %s",
                    request.method,
                    request.url.path,
                    response.status_code,
                    extra={
                        "http_method": request.method,
                        "path": request.url.path,
                        "status_code": response.status_code,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )
        return response
