"""Durable jobs, append-only audit log, saved reconciliations and aliases.

Revision ID: 20260927_0001
Revises: 20260801_0001
Create Date: 2026-09-27
"""

from alembic import op
import sqlalchemy as sa


revision = "20260927_0001"
down_revision = "20260801_0001"
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    with op.batch_alter_table("reconciliation_jobs") as batch:
        batch.add_column(sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("worker_id", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("template_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("template_version", sa.Integer(), nullable=True))
    op.create_index("ix_reconciliation_jobs_template_id", "reconciliation_jobs", ["template_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("actor_type", sa.String(length=30), nullable=False),
        sa.Column("actor_id", sa.String(length=120), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.String(length=120), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("before_json", sa.Text(), nullable=True),
        sa.Column("after_json", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index("ix_audit_events_scope_id", "audit_events", ["scope_id"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_entity_id", "audit_events", ["entity_id"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from app.models.audit import POSTGRES_GUARDS

        for statement in POSTGRES_GUARDS:
            op.execute(statement)
    elif bind.dialect.name == "sqlite":
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events "
            "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
        )
        op.execute(
            "CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events "
            "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
        )

    op.create_table(
        "reconciliation_templates",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reconciliation_templates_session_id", "reconciliation_templates", ["session_id"])

    op.create_table(
        "reconciliation_template_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["reconciliation_templates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id", "version", name="uq_template_version"),
    )
    op.create_index("ix_reconciliation_template_versions_template_id", "reconciliation_template_versions", ["template_id"])

    op.create_table(
        "entity_aliases",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("canonical", sa.String(length=300), nullable=False),
        sa.Column("variant", sa.String(length=300), nullable=False),
        sa.Column("canonical_key", sa.String(length=300), nullable=False),
        sa.Column("variant_key", sa.String(length=300), nullable=False),
        sa.Column("column_hint", sa.String(length=200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_entity_aliases_scope_id", "entity_aliases", ["scope_id"])
    op.create_index("ix_entity_aliases_canonical_key", "entity_aliases", ["canonical_key"])
    op.create_index("ix_entity_aliases_variant_key", "entity_aliases", ["variant_key"])

    op.create_table(
        "match_decisions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("column_hint", sa.String(length=200), nullable=False),
        sa.Column("value_1", sa.String(length=300), nullable=False),
        sa.Column("value_2", sa.String(length=300), nullable=False),
        sa.Column("pair_key", sa.String(length=620), nullable=False),
        sa.Column("decision", sa.String(length=10), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.String(length=120), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_match_decisions_scope_id", "match_decisions", ["scope_id"])
    op.create_index("ix_match_decisions_job_id", "match_decisions", ["job_id"])
    op.create_index("ix_match_decisions_pair_key", "match_decisions", ["pair_key"])


def downgrade() -> None:
    for index, table in (
        ("ix_match_decisions_pair_key", "match_decisions"),
        ("ix_match_decisions_job_id", "match_decisions"),
        ("ix_match_decisions_scope_id", "match_decisions"),
    ):
        op.drop_index(index, table_name=table)
    op.drop_table("match_decisions")
    op.drop_index("ix_entity_aliases_variant_key", table_name="entity_aliases")
    op.drop_index("ix_entity_aliases_canonical_key", table_name="entity_aliases")
    op.drop_index("ix_entity_aliases_scope_id", table_name="entity_aliases")
    op.drop_table("entity_aliases")
    op.drop_index("ix_reconciliation_template_versions_template_id", table_name="reconciliation_template_versions")
    op.drop_table("reconciliation_template_versions")
    op.drop_index("ix_reconciliation_templates_session_id", table_name="reconciliation_templates")
    op.drop_table("reconciliation_templates")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_change ON audit_events")
        op.execute("DROP FUNCTION IF EXISTS audit_events_append_only()")
    op.drop_index("ix_audit_events_entity_id", table_name="audit_events")
    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_index("ix_audit_events_scope_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_reconciliation_jobs_template_id", table_name="reconciliation_jobs")
    with op.batch_alter_table("reconciliation_jobs") as batch:
        for column in ("template_version", "template_id", "worker_id", "heartbeat_at", "attempts"):
            batch.drop_column(column)
