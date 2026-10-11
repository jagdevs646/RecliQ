"""Add workspace-scoped support tickets and conversation messages.

Revision ID: 20261011_0001
Revises: 20261010_0001
Create Date: 2026-10-11
"""

from alembic import op
import sqlalchemy as sa


revision = "20261011_0001"
down_revision = "20261010_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "support_tickets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("requester_user_id", sa.String(length=36), nullable=True),
        sa.Column("requester_type", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("custom_area", sa.String(length=120), server_default="", nullable=False),
        sa.Column("workflow_stage", sa.String(length=40), nullable=False),
        sa.Column("impact", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("expected_behavior", sa.Text(), server_default="", nullable=False),
        sa.Column("reproduction_steps", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="new", nullable=False),
        sa.Column("priority", sa.String(length=16), server_default="normal", nullable=False),
        sa.Column("admin_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_tickets_scope_id", "support_tickets", ["scope_id"])
    op.create_index("ix_support_tickets_requester_user_id", "support_tickets", ["requester_user_id"])
    op.create_index("ix_support_tickets_category", "support_tickets", ["category"])
    op.create_index("ix_support_tickets_status", "support_tickets", ["status"])
    op.create_index("ix_support_tickets_priority", "support_tickets", ["priority"])
    op.create_index("ix_support_tickets_scope_updated", "support_tickets", ["scope_id", "updated_at"])
    op.create_index("ix_support_tickets_status_priority", "support_tickets", ["status", "priority"])
    op.create_table(
        "support_ticket_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("ticket_id", sa.String(length=36), nullable=False),
        sa.Column("author_type", sa.String(length=16), nullable=False),
        sa.Column("author_user_id", sa.String(length=36), nullable=True),
        sa.Column("author_label", sa.String(length=120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["support_tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_support_ticket_messages_ticket_id", "support_ticket_messages", ["ticket_id"])
    op.create_index("ix_support_ticket_messages_author_user_id", "support_ticket_messages", ["author_user_id"])
    op.create_index("ix_support_ticket_messages_ticket_created", "support_ticket_messages", ["ticket_id", "created_at"])


def downgrade() -> None:
    op.drop_table("support_ticket_messages")
    op.drop_table("support_tickets")
