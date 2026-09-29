from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class ResolutionRule(IdMixin, TimestampMixin, Base):
    """A person-defined rule that explains a recurring exception, e.g. "bank
    charges up to 500 go to GL 6100". Rules run after matching, in priority
    order; they never change which records match. Every change bumps
    ``version`` and is written to the audit log with before/after values."""

    __tablename__ = "resolution_rules"

    scope_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    conditions_json: Mapped[str] = mapped_column(Text, nullable=False)
    action_json: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(120), nullable=False)


class ExceptionPattern(IdMixin, TimestampMixin, Base):
    """A kind of unresolved exception seen across runs (e.g. "only in the bank
    statement, narrative 'bank charges'"). Patterns seen in several different
    reconciliations are offered as rule suggestions; nothing is automatic."""

    __tablename__ = "exception_patterns"
    __table_args__ = (UniqueConstraint("scope_id", "category", "column_name", "signature", name="uq_exception_pattern"),)

    scope_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    column_name: Mapped[str] = mapped_column(String(200), nullable=False)
    signature: Mapped[str] = mapped_column(String(200), nullable=False)
    example: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    jobs_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_abs_amount: Mapped[float | None] = mapped_column(Float)
    amount_column: Mapped[str | None] = mapped_column(String(200))
    last_job_id: Mapped[str | None] = mapped_column(String(36))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
