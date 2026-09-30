from pydantic import BaseModel

from app.utils.timestamps import UtcDatetime


class UploadedFileOut(BaseModel):
    id: str
    original_filename: str
    content_type: str | None
    size_bytes: int
    storage_backend: str
    created_at: UtcDatetime

    model_config = {"from_attributes": True}


class FileColumnsResponse(BaseModel):
    file_id: str
    sheet_id: str | None = None
    orientation: str
    columns: list[str]


class SheetMetadata(BaseModel):
    id: str
    name: str


class FileMetadataResponse(BaseModel):
    file_id: str
    filename: str
    sheets: list[SheetMetadata]
