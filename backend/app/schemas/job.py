from datetime import datetime

from pydantic import BaseModel


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
    report_id: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class JobListResponse(BaseModel):
    jobs: list[ReconciliationJobOut]

