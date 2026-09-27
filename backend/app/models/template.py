from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin, utc_now


class ReconciliationTemplate(IdMixin, TimestampMixin, Base):
    """A saved, reusable reconciliation setup. Its configuration lives in
    immutable versions; editing a template adds a version."""

    __tablename__ = "reconciliation_templates"

    session_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    versions = relationship(
        "ReconciliationTemplateVersion",
        back_populates="template",
        order_by="ReconciliationTemplateVersion.version",
        cascade="all, delete-orphan",
    )


class ReconciliationTemplateVersion(Base):
    __tablename__ = "reconciliation_template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_template_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_templates.id"), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    change_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)

    template = relationship("ReconciliationTemplate", back_populates="versions")
