from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, utc_now


class SupportTicket(IdMixin, Base):
    __tablename__ = "support_tickets"
    __table_args__ = (
        Index("ix_support_tickets_scope_updated", "scope_id", "updated_at"),
        Index("ix_support_tickets_status_priority", "status", "priority"),
    )

    scope_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    requester_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    requester_type: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    custom_area: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    workflow_stage: Mapped[str] = mapped_column(String(40), nullable=False)
    impact: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    expected_behavior: Mapped[str] = mapped_column(Text, default="", nullable=False)
    reproduction_steps: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="new", index=True, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="normal", index=True, nullable=False)
    admin_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class SupportTicketMessage(IdMixin, Base):
    __tablename__ = "support_ticket_messages"
    __table_args__ = (Index("ix_support_ticket_messages_ticket_created", "ticket_id", "created_at"),)

    ticket_id: Mapped[str] = mapped_column(ForeignKey("support_tickets.id", ondelete="CASCADE"), index=True, nullable=False)
    author_type: Mapped[str] = mapped_column(String(16), nullable=False)
    author_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    author_label: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
