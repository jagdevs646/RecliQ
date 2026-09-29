"""Learning from decisions and auto-resolution rules.

Revision ID: 20260929_0001
Revises: 20260927_0001
Create Date: 2026-09-29
"""

from alembic import op
import sqlalchemy as sa


revision = "20260929_0001"
down_revision = "20260927_0001"
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    with op.batch_alter_table("match_decisions") as batch:
        batch.add_column(sa.Column("method", sa.String(length=80), nullable=True))
    op.create_index("ix_match_decisions_method", "match_decisions", ["method"])

    op.create_table(
        "resolution_rules",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("conditions_json", sa.Text(), nullable=False),
        sa.Column("action_json", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=120), nullable=False),
        sa.Column("updated_by", sa.String(length=120), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_resolution_rules_scope_id", "resolution_rules", ["scope_id"])

    op.create_table(
        "exception_patterns",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("column_name", sa.String(length=200), nullable=False),
        sa.Column("signature", sa.String(length=200), nullable=False),
        sa.Column("example", sa.String(length=300), nullable=False),
        sa.Column("jobs_seen", sa.Integer(), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("max_abs_amount", sa.Float(), nullable=True),
        sa.Column("amount_column", sa.String(length=200), nullable=True),
        sa.Column("last_job_id", sa.String(length=36), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_id", "category", "column_name", "signature", name="uq_exception_pattern"),
    )
    op.create_index("ix_exception_patterns_scope_id", "exception_patterns", ["scope_id"])


def downgrade() -> None:
    op.drop_index("ix_exception_patterns_scope_id", table_name="exception_patterns")
    op.drop_table("exception_patterns")
    op.drop_index("ix_resolution_rules_scope_id", table_name="resolution_rules")
    op.drop_table("resolution_rules")
    op.drop_index("ix_match_decisions_method", table_name="match_decisions")
    with op.batch_alter_table("match_decisions") as batch:
        batch.drop_column("method")
