from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class EntityAlias(IdMixin, TimestampMixin, Base):
    """An organization-approved equivalence, e.g. "IBM" = "International
    Business Machines". Only people create active aliases; the system can
    propose them from decisions but never activates one by itself."""

    __tablename__ = "entity_aliases"

    scope_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    canonical: Mapped[str] = mapped_column(String(300), nullable=False)
    variant: Mapped[str] = mapped_column(String(300), nullable=False)
    # Normalized forms, so lookups ignore case and punctuation.
    canonical_key: Mapped[str] = mapped_column(String(300), index=True, nullable=False)
    variant_key: Mapped[str] = mapped_column(String(300), index=True, nullable=False)
    column_hint: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)
    source: Mapped[str] = mapped_column(String(30), default="manual", nullable=False)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)


class MatchDecision(IdMixin, TimestampMixin, Base):
    """A reviewer's accept or reject of a proposed match between two values.

    Decisions are evidence for alias suggestions. A value pair is suggested
    only after repeated acceptance and no rejection, and still needs approval.
    """

    __tablename__ = "match_decisions"

    scope_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(36), index=True)
    column_hint: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    value_1: Mapped[str] = mapped_column(String(300), nullable=False)
    value_2: Mapped[str] = mapped_column(String(300), nullable=False)
    pair_key: Mapped[str] = mapped_column(String(620), index=True, nullable=False)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)  # "accept" or "reject"
    confidence: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    decided_by: Mapped[str] = mapped_column(String(120), nullable=False)
