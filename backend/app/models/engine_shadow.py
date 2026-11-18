"""Persistent evidence from the controlled row-versus-column engine rollout."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class EngineShadowComparison(IdMixin, TimestampMixin, Base):
    """One private shadow comparison for one reconciliation job.

    The user-facing report always comes from the primary row engine.  This
    record holds only rollout evidence and points at a session-owned JSONL
    artifact when the two engines differ.
    """

    __tablename__ = "engine_shadow_comparisons"
    __table_args__ = (
        Index("ix_engine_shadow_comparisons_session_created", "session_id", "created_at"),
        Index("ix_engine_shadow_comparisons_status_created", "status", "created_at"),
    )

    job_id: Mapped[str] = mapped_column(
        ForeignKey("reconciliation_jobs.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    primary_engine: Mapped[str] = mapped_column(String(16), nullable=False)
    shadow_engine: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    rule_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    compared_rule_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    skipped_rule_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    difference_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    primary_seconds: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    shadow_seconds: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    comparison_seconds: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    details_storage_path: Mapped[str | None] = mapped_column(String(1000))
    details_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    failure_summary: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
