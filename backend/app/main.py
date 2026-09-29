import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import aliases, analysis, audit, files, health, jobs, learning, reconciliation, reports, resolution, session, templates
from app.core.anonymous_session import AnonymousSessionMiddleware
from app.core.config import get_settings
from app.core.observability import RequestContextMiddleware, configure_logging, init_error_tracking
from app.core.upload_validation import BodySizeLimitMiddleware
from app.database.base import Base
from app.database.session import add_missing_sqlite_columns, engine, migrate_legacy_owner_columns


settings = get_settings()
logger = logging.getLogger("recliq")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level, settings.log_format)
    init_error_tracking(settings.sentry_dsn, settings.environment, settings.release, settings.sentry_traces_sample_rate)
    if settings.database_url.startswith("sqlite"):
        migrate_legacy_owner_columns()
        Base.metadata.create_all(bind=engine)
        add_missing_sqlite_columns()
    if settings.job_queue_backend.lower() != "redis":
        # In-process jobs die with the process: pick up what a restart left
        # behind, and keep checking for stalled jobs. Redis workers do this
        # themselves (app.jobs.worker).
        from app.jobs.recovery import recover_orphaned_jobs, start_sweeper, stop_sweeper

        try:
            recover_orphaned_jobs(requeue_all_queued=True)
        except Exception:
            logger.exception("Startup job recovery failed")
        start_sweeper()
        yield
        stop_sweeper()
    else:
        yield


def create_app() -> FastAPI:
    app = FastAPI(title=f"{settings.app_name} API", version="1.0.0", lifespan=lifespan)

    # Build the full CORS origins list from config + optional extras.
    cors_origins = [
        settings.frontend_origin,
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
    ]
    if settings.extra_cors_origins:
        for origin in settings.extra_cors_origins.split(","):
            origin = origin.strip()
            if origin and origin not in cors_origins:
                cors_origins.append(origin)

    # Added first = innermost: request logging sees the session ID set by the
    # session middleware, and CORS headers are added to error responses too.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Session-ID", "X-Request-ID", "Content-Disposition"],
    )
    app.add_middleware(AnonymousSessionMiddleware)
    # Outermost: refuse oversized uploads before they are read.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_upload_bytes + 1024 * 1024, paths=("/files/upload",))

    app.include_router(health.router)
    app.include_router(session.router, prefix=settings.api_prefix)
    app.include_router(files.router, prefix=settings.api_prefix)
    app.include_router(reconciliation.router, prefix=settings.api_prefix)
    app.include_router(analysis.router, prefix=settings.api_prefix)
    app.include_router(jobs.router, prefix=settings.api_prefix)
    app.include_router(reports.router, prefix=settings.api_prefix)
    app.include_router(templates.router, prefix=settings.api_prefix)
    app.include_router(aliases.router, prefix=settings.api_prefix)
    app.include_router(audit.router, prefix=settings.api_prefix)
    app.include_router(resolution.router, prefix=settings.api_prefix)
    app.include_router(learning.router, prefix=settings.api_prefix)
    return app


app = create_app()
