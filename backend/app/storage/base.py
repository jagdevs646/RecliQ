from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from fastapi import UploadFile


REPORT_DATA_CATEGORIES = (
    "exceptions",
    "matched_records",
    "missing_in_file_1",
    "missing_in_file_2",
    "field_differences",
    "identity_resolution",
    "auto_resolved",
)


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


def report_data_directory(report_storage_path: str) -> str:
    path = PurePosixPath(report_storage_path)
    return str(path.with_name(f"{path.stem}_data"))


def report_data_manifest_path(report_storage_path: str) -> str:
    return f"{report_data_directory(report_storage_path)}/summary.json"


def report_data_category_path(report_storage_path: str, category: str) -> str:
    if category not in REPORT_DATA_CATEGORIES:
        raise ValueError(f"Unknown report data category: {category}")
    return f"{report_data_directory(report_storage_path)}/{category}.parquet"


def report_data_storage_paths(report_storage_path: str) -> tuple[str, ...]:
    """New table files plus the legacy JSON path, for migration and cleanup."""
    return (
        report_data_path(report_storage_path),
        report_data_manifest_path(report_storage_path),
        *(report_data_category_path(report_storage_path, category) for category in REPORT_DATA_CATEGORIES),
    )


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
