"""Widen the two-factor key columns for encrypted values.

Encrypted keys (``enc1:`` + base64 of nonce, ciphertext and tag) are longer than
the plain base32 keys. The application encrypts existing plain keys at start-up
once MFA_ENCRYPTION_KEY is available, so this revision changes no data.

Revision ID: 20261012_0001
Revises: 20261011_0002
Create Date: 2026-10-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20261012_0001"
down_revision = "20261011_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column("mfa_secret", existing_type=sa.String(length=128), type_=sa.String(length=255), existing_nullable=True)
        batch.alter_column("mfa_pending_secret", existing_type=sa.String(length=128), type_=sa.String(length=255), existing_nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column("mfa_pending_secret", existing_type=sa.String(length=255), type_=sa.String(length=128), existing_nullable=True)
        batch.alter_column("mfa_secret", existing_type=sa.String(length=255), type_=sa.String(length=128), existing_nullable=True)
