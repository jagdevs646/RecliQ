"""Store controlled row-versus-column engine rollout evidence.

Revision ID: 20261022_0001
Revises: 20261015_0001
Create Date: 2026-10-22
"""

from alembic import op
import sqlalchemy as sa


revision = "20261022_0001"
down_revision = "20261015_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "engine_shadow_comparisons",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("primary_engine", sa.String(length=16), nullable=False),
        sa.Column("shadow_engine", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("rule_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("compared_rule_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped_rule_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("difference_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("primary_seconds", sa.Float(), server_default="0", nullable=False),
        sa.Column("shadow_seconds", sa.Float(), server_default="0", nullable=False),
        sa.Column("comparison_seconds", sa.Float(), server_default="0", nullable=False),
        sa.Column("details_storage_path", sa.String(length=1000), nullable=True),
        sa.Column("details_size_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("failure_summary", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["reconciliation_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index("ix_engine_shadow_comparisons_job_id", "engine_shadow_comparisons", ["job_id"])
    op.create_index("ix_engine_shadow_comparisons_session_id", "engine_shadow_comparisons", ["session_id"])
    op.create_index("ix_engine_shadow_comparisons_status", "engine_shadow_comparisons", ["status"])
    op.create_index(
        "ix_engine_shadow_comparisons_session_created",
        "engine_shadow_comparisons",
        ["session_id", "created_at"],
    )
    op.create_index(
        "ix_engine_shadow_comparisons_status_created",
        "engine_shadow_comparisons",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_engine_shadow_comparisons_status_created", table_name="engine_shadow_comparisons")
    op.drop_index("ix_engine_shadow_comparisons_session_created", table_name="engine_shadow_comparisons")
    op.drop_index("ix_engine_shadow_comparisons_status", table_name="engine_shadow_comparisons")
    op.drop_index("ix_engine_shadow_comparisons_session_id", table_name="engine_shadow_comparisons")
    op.drop_index("ix_engine_shadow_comparisons_job_id", table_name="engine_shadow_comparisons")
    op.drop_table("engine_shadow_comparisons")
