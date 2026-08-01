"""Rename shared MVP ownership to anonymous session ownership."""

from alembic import op


revision = "20260801_0001"
down_revision = "20260729_0001"
branch_labels = None
depends_on = None


TABLES = ("uploaded_files", "reports", "reconciliation_jobs", "reconciliation_history")


def upgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column("owner_id", new_column_name="session_id")
        op.drop_index(f"ix_{table}_owner_id", table_name=table)
        op.create_index(f"ix_{table}_session_id", table, ["session_id"])


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_index(f"ix_{table}_session_id", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.alter_column("session_id", new_column_name="owner_id")
        op.create_index(f"ix_{table}_owner_id", table, ["owner_id"])
