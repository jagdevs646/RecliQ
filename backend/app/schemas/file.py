from datetime import datetime

from pydantic import BaseModel


class UploadedFileOut(BaseModel):
    id: str
    original_filename: str
    content_type: str | None
    size_bytes: int
    storage_backend: str
    created_at: datetime

    model_config = {"from_attributes": True}


class FileColumnsResponse(BaseModel):
    file_id: str
    orientation: str
    columns: list[str]

