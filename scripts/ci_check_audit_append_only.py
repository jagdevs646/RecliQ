"""CI check: on PostgreSQL the audit log refuses UPDATE, DELETE and TRUNCATE.

Run after ``alembic upgrade head`` with DATABASE_URL pointing at the database.
"""
import os

from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])
with engine.begin() as connection:
    connection.execute(text(
        "INSERT INTO audit_events (event_id, occurred_at, scope_id, actor_type, actor_id, action, entity_type, "
        "summary, metadata_json, prev_hash, hash) VALUES ('ci-check', now(), 'ci', 'system', 'ci', 'ci.check', "
        "'ci', '', '{}', '0', '0')"
    ))

for statement in ("UPDATE audit_events SET summary = 'x'", "DELETE FROM audit_events", "TRUNCATE audit_events"):
    try:
        with engine.begin() as connection:
            connection.execute(text(statement))
    except Exception as exc:  # The trigger raises; anything else is a real failure.
        if "append-only" not in str(exc):
            raise
    else:
        raise SystemExit(f"{statement} was not blocked")
print("audit_events is append-only")
