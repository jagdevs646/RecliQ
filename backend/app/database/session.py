from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def migrate_legacy_owner_columns() -> None:
    """Upgrade the local MVP database without exposing legacy demo rows.

    Alembic is the deployment migration path. This small SQLite compatibility
    step keeps an existing local installation bootable after the ownership
    column was renamed from ``owner_id`` to ``session_id``.
    """
    if not settings.database_url.startswith("sqlite"):
        return
    tables = ("uploaded_files", "reports", "reconciliation_jobs", "reconciliation_history")
    inspector = inspect(engine)
    for table in tables:
        if table not in inspector.get_table_names():
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        if "owner_id" not in columns or "session_id" in columns:
            continue
        old_index = f"ix_{table}_owner_id"
        new_index = f"ix_{table}_session_id"
        with engine.begin() as connection:
            connection.execute(text(f'ALTER TABLE "{table}" RENAME COLUMN "owner_id" TO "session_id"'))
            connection.execute(text(f'DROP INDEX IF EXISTS "{old_index}"'))
            connection.execute(text(f'CREATE INDEX IF NOT EXISTS "{new_index}" ON "{table}" ("session_id")'))
