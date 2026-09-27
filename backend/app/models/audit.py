from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_id, utc_now


class AuditEvent(Base):
    """One immutable audit record.

    Rows are append-only: the ORM refuses updates and deletes, and database
    triggers do the same for direct SQL. Each row stores the hash of the
    previous row in its scope, so any later edit or removal breaks the chain
    and is detected by verification.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, default=new_id, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    # The chain scope: the browser session today, the organization once
    # accounts exist.
    scope_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(120), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    request_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(120), index=True)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    before_json: Mapped[str | None] = mapped_column(Text)
    after_json: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)


# Database-level append-only enforcement, created with the table (the
# migration creates the same triggers for PostgreSQL deployments).
from sqlalchemy import DDL, event  # noqa: E402

_SQLITE_GUARDS = (
    "CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events "
    "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events "
    "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END",
)
POSTGRES_GUARDS = (
    "CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION 'audit_events is append-only'; END; $$ LANGUAGE plpgsql",
    "CREATE TRIGGER audit_events_no_change BEFORE UPDATE OR DELETE ON audit_events "
    "FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()",
    "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
    "FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only()",
)
for _statement in _SQLITE_GUARDS:
    event.listen(AuditEvent.__table__, "after_create", DDL(_statement).execute_if(dialect="sqlite"))
for _statement in POSTGRES_GUARDS:
    event.listen(AuditEvent.__table__, "after_create", DDL(_statement).execute_if(dialect="postgresql"))


@event.listens_for(AuditEvent, "before_update")
def _refuse_update(mapper, connection, target) -> None:
    raise PermissionError("Audit events are append-only and cannot be modified.")


@event.listens_for(AuditEvent, "before_delete")
def _refuse_delete(mapper, connection, target) -> None:
    raise PermissionError("Audit events are append-only and cannot be deleted.")
