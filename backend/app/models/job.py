from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin


class ReconciliationJob(IdMixin, TimestampMixin, Base):
    __tablename__ = "reconciliation_jobs"

    session_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    orientation: Mapped[str] = mapped_column(String(30), default="vertical", nullable=False)
    settings_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    input_file_1_id: Mapped[str | None] = mapped_column(ForeignKey("uploaded_files.id"))
    input_file_2_id: Mapped[str | None] = mapped_column(ForeignKey("uploaded_files.id"))
    report_id: Mapped[str | None] = mapped_column(ForeignKey("reports.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    report = relationship("Report", foreign_keys=[report_id], post_update=True)
    input_file_1 = relationship("UploadedFile", foreign_keys=[input_file_1_id])
    input_file_2 = relationship("UploadedFile", foreign_keys=[input_file_2_id])

    @property
    def input_file_1_name(self) -> str | None:
        return self.input_file_1.original_filename if self.input_file_1 else None

    @property
    def input_file_2_name(self) -> str | None:
        return self.input_file_2.original_filename if self.input_file_2 else None
