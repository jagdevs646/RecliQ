"""Record per-account administrator approval for email verification.

Revision ID: 20261015_0001
Revises: 20261012_0002
Create Date: 2026-10-15
"""

from alembic import op
import sqlalchemy as sa


revision = "20261015_0001"
down_revision = "20261012_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column(
            "email_verification_approved_by_admin",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ))
        batch.add_column(sa.Column("email_verification_approved_by", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("email_verification_approved_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("email_verification_approved_at")
        batch.drop_column("email_verification_approved_by")
        batch.drop_column("email_verification_approved_by_admin")
