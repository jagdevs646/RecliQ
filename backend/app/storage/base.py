from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from fastapi import UploadFile


@dataclass(frozen=True)
class StoredObject:
    original_filename: str
    stored_filename: str
    storage_backend: str
    storage_path: str
    size_bytes: int
    content_type: str | None = None


def report_data_path(report_storage_path: str) -> str:
    """Storage path of the JSON data saved beside a report (custom reports and
    previews are rebuilt from it)."""
    path = PurePosixPath(report_storage_path)
    return str(path.with_name(f"{path.stem}_data.json"))


class StorageBackend(Protocol):
    name: str

    def save_upload(self, file: UploadFile, session_id: str, content_type: str | None = None) -> StoredObject:
        ...

    def save_file(self, source_path: Path, storage_path: str) -> None:
        ...

    def save_report(self, source_path: Path, session_id: str, filename: str) -> StoredObject:
        ...

    def resolve_path(self, storage_path: str) -> Path:
        ...

    def delete_file(self, storage_path: str) -> bool:
        ...
