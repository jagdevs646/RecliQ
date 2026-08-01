from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import files, health, jobs, reconciliation, reports, session
from app.core.anonymous_session import AnonymousSessionMiddleware
from app.core.config import get_settings
from app.database.base import Base
from app.database.session import engine, migrate_legacy_owner_columns


settings = get_settings()


def create_app() -> FastAPI:
    app = FastAPI(title=f"{settings.app_name} API", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            settings.frontend_origin,
            "http://localhost:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:5174",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Session-ID"],
    )
    app.add_middleware(AnonymousSessionMiddleware)

    app.include_router(health.router)
    app.include_router(session.router, prefix=settings.api_prefix)
    app.include_router(files.router, prefix=settings.api_prefix)
    app.include_router(reconciliation.router, prefix=settings.api_prefix)
    app.include_router(jobs.router, prefix=settings.api_prefix)
    app.include_router(reports.router, prefix=settings.api_prefix)
    return app


app = create_app()


@app.on_event("startup")
def create_local_tables() -> None:
    if settings.database_url.startswith("sqlite"):
        migrate_legacy_owner_columns()
        Base.metadata.create_all(bind=engine)
