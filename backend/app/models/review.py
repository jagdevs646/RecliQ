from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now


class ReviewOutcome(Base):
    """Current resolution of a review pair; MatchDecision keeps the audit history."""

    __tablename__ = "review_outcomes"

    job_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_jobs.id"), primary_key=True)
    pair_key: Mapped[str] = mapped_column(String(620), primary_key=True)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
