"""Use 64-bit storage counters for workspace quotas and generated artifacts.

Revision ID: 20261010_0001
Revises: 20261009_0001
Create Date: 2026-10-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20261010_0001"
down_revision = "20261009_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, column in (
        ("uploaded_files", "size_bytes"),
        ("reports", "size_bytes"),
        ("reports", "data_size_bytes"),
        ("workspace_quotas", "storage_bytes"),
    ):
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, existing_type=sa.Integer(), type_=sa.BigInteger(), nullable=False)


def downgrade() -> None:
    for table, column in (
        ("workspace_quotas", "storage_bytes"),
        ("reports", "data_size_bytes"),
        ("reports", "size_bytes"),
        ("uploaded_files", "size_bytes"),
    ):
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, existing_type=sa.BigInteger(), type_=sa.Integer(), nullable=False)
