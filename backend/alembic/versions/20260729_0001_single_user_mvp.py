"""Single-user RecliQ MVP schema.

Revision ID: 20260729_0001
Revises:
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa


revision = "20260729_0001"
down_revision = None
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "uploaded_files",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(length=80), nullable=False),
        sa.Column("original_filename", sa.String(length=500), nullable=False),
        sa.Column("stored_filename", sa.String(length=500), nullable=False),
        sa.Column("storage_backend", sa.String(length=50), nullable=False),
        sa.Column("storage_path", sa.String(length=1000), nullable=False),
        sa.Column("content_type", sa.String(length=150), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_uploaded_files_owner_id", "uploaded_files", ["owner_id"])

    op.create_table(
        "reports",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(length=80), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("storage_backend", sa.String(length=50), nullable=False),
        sa.Column("storage_path", sa.String(length=1000), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("summary_json", sa.Text(), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reports_owner_id", "reports", ["owner_id"])

    op.create_table(
        "reconciliation_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(length=80), nullable=False),
        sa.Column("job_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("orientation", sa.String(length=30), nullable=False),
        sa.Column("settings_json", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("input_file_1_id", sa.String(), nullable=True),
        sa.Column("input_file_2_id", sa.String(), nullable=True),
        sa.Column("report_id", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(["input_file_1_id"], ["uploaded_files.id"]),
        sa.ForeignKeyConstraint(["input_file_2_id"], ["uploaded_files.id"]),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reconciliation_jobs_owner_id", "reconciliation_jobs", ["owner_id"])
    op.create_index("ix_reconciliation_jobs_status", "reconciliation_jobs", ["status"])

    op.create_table(
        "reconciliation_history",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(length=80), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["job_id"], ["reconciliation_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reconciliation_history_job_id", "reconciliation_history", ["job_id"])
    op.create_index("ix_reconciliation_history_owner_id", "reconciliation_history", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_reconciliation_history_owner_id", table_name="reconciliation_history")
    op.drop_index("ix_reconciliation_history_job_id", table_name="reconciliation_history")
    op.drop_table("reconciliation_history")
    op.drop_index("ix_reconciliation_jobs_status", table_name="reconciliation_jobs")
    op.drop_index("ix_reconciliation_jobs_owner_id", table_name="reconciliation_jobs")
    op.drop_table("reconciliation_jobs")
    op.drop_index("ix_reports_owner_id", table_name="reports")
    op.drop_table("reports")
    op.drop_index("ix_uploaded_files_owner_id", table_name="uploaded_files")
    op.drop_table("uploaded_files")
