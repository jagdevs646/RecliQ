from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.file import UploadedFileOut


class ReportOut(BaseModel):
    id: str
    filename: str
    size_bytes: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ReconciliationJobOut(BaseModel):
    id: str
    job_type: str
    status: str
    progress: int
    orientation: str
    error_message: str | None
    input_file_1_id: str | None
    input_file_2_id: str | None
    input_file_1_name: str | None = None
    input_file_2_name: str | None = None
    file_pair_count: int = 1
    report_id: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    attempts: int = 0
    template_id: str | None = None
    template_version: int | None = None

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    jobs: list[ReconciliationJobOut]


class JobPlanResponse(BaseModel):
    """A finished run's setup, to change its rules and run it again on the same files."""

    job_id: str
    orientation: str
    file_pairs: list[dict[str, Any]]
    files: list[UploadedFileOut]

