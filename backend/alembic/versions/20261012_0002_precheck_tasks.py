"""Persist asynchronous data-quality prechecks.

Revision ID: 20261012_0002
Revises: 20261012_0001
Create Date: 2026-10-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20261012_0002"
down_revision = "20261012_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_prechecks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reconciliation_prechecks_session_id", "reconciliation_prechecks", ["session_id"])
    op.create_index("ix_reconciliation_prechecks_status", "reconciliation_prechecks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_reconciliation_prechecks_status", table_name="reconciliation_prechecks")
    op.drop_index("ix_reconciliation_prechecks_session_id", table_name="reconciliation_prechecks")
    op.drop_table("reconciliation_prechecks")
