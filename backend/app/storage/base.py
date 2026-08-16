from dataclasses import dataclass
from pathlib import Path
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


class StorageBackend(Protocol):
    name: str

    def save_upload(self, file: UploadFile, session_id: str) -> StoredObject:
        ...

    def save_report(self, source_path: Path, session_id: str, filename: str) -> StoredObject:
        ...

    def resolve_path(self, storage_path: str) -> Path:
        ...
