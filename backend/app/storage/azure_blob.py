import hashlib
import os
from shutil import copyfileobj
from pathlib import Path, PurePosixPath
from tempfile import gettempdir
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import Settings
from app.storage.base import StoredObject


class AzureBlobStorage:
    """Durable object storage. Blobs are written once under unique names, so a
    downloaded copy stays valid: it is cached locally (one directory per blob
    folder) and the parsed-sheet cache beside it keeps working."""

    name = "azure"

    def __init__(self, settings: Settings):
        if not settings.azure_storage_connection_string:
            raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is required when STORAGE_BACKEND=azure")
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError as exc:  # pragma: no cover - depends on optional Azure package.
            raise RuntimeError("Install azure-storage-blob to use Azure Blob Storage") from exc
        self.container_name = settings.azure_storage_container
        self.client = BlobServiceClient.from_connection_string(settings.azure_storage_connection_string)
        self.container = self.client.get_container_client(self.container_name)
        if not self.container.exists():
            self.container.create_container()
        self.cache_root = Path(gettempdir()) / "recliq-blob-cache" / self.container_name

    def _cache_path(self, storage_path: str) -> Path:
        path = PurePosixPath(storage_path)
        folder = hashlib.sha1(str(path.parent).encode("utf-8")).hexdigest()[:16]
        return self.cache_root / folder / path.name

    def _remember(self, storage_path: str, data: bytes) -> None:
        cached = self._cache_path(storage_path)
        cached.parent.mkdir(parents=True, exist_ok=True)
        partial = cached.with_name(f"{cached.name}.{os.getpid()}.part")
        partial.write_bytes(data)
        os.replace(partial, cached)

    def _remember_stream(self, storage_path: str, stream) -> Path:
        cached = self._cache_path(storage_path)
        cached.parent.mkdir(parents=True, exist_ok=True)
        partial = cached.with_name(f"{cached.name}.{os.getpid()}.part")
        stream.seek(0)
        try:
            with partial.open("wb") as target:
                copyfileobj(stream, target, length=1024 * 1024)
            os.replace(partial, cached)
        finally:
            partial.unlink(missing_ok=True)
        return cached

    def save_upload(self, file: UploadFile, session_id: str, content_type: str | None = None) -> StoredObject:
        suffix = Path(file.filename or "upload.xlsx").suffix or ".xlsx"
        stored_filename = f"{uuid4()}{suffix}"
        storage_path = f"uploads/{str(session_id)}/{stored_filename}"
        file.file.seek(0)
        self.container.upload_blob(storage_path, file.file, overwrite=True, max_concurrency=2)
        cached = self._remember_stream(storage_path, file.file)
        return StoredObject(
            original_filename=file.filename or stored_filename,
            stored_filename=stored_filename,
            storage_backend=self.name,
            storage_path=storage_path,
            size_bytes=cached.stat().st_size,
            content_type=content_type or file.content_type,
        )

    def save_report(self, source_path: Path, session_id: str, filename: str) -> StoredObject:
        stored_filename = f"{uuid4()}-{filename}"
        storage_path = f"reports/{str(session_id)}/{stored_filename}"
        with source_path.open("rb") as source:
            self.container.upload_blob(storage_path, source, overwrite=True, max_concurrency=2)
        return StoredObject(
            original_filename=filename,
            stored_filename=stored_filename,
            storage_backend=self.name,
            storage_path=storage_path,
            size_bytes=source_path.stat().st_size,
            content_type=("application/zip" if filename.lower().endswith(".zip") else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        )

    def save_file(self, source_path: Path, storage_path: str) -> None:
        with source_path.open("rb") as source:
            self.container.upload_blob(storage_path, source, overwrite=True, max_concurrency=2)
        with source_path.open("rb") as source:
            self._remember_stream(storage_path, source)

    def resolve_path(self, storage_path: str) -> Path:
        cached = self._cache_path(storage_path)
        if cached.exists():
            return cached
        from azure.core.exceptions import ResourceNotFoundError

        try:
            downloader = self.container.download_blob(storage_path)
        except ResourceNotFoundError as exc:
            raise FileNotFoundError(storage_path) from exc
        cached.parent.mkdir(parents=True, exist_ok=True)
        partial = cached.with_name(f"{cached.name}.{os.getpid()}.part")
        try:
            with partial.open("wb") as handle:
                downloader.readinto(handle)
            os.replace(partial, cached)
        finally:
            partial.unlink(missing_ok=True)
        return cached

    def delete_file(self, storage_path: str) -> bool:
        cached = self._cache_path(storage_path)
        if cached.exists():
            from app.reconciliation_engine.ingestion import clear_table_cache

            clear_table_cache(cached)
            cached.unlink(missing_ok=True)
        try:
            blob_client = self.container.get_blob_client(storage_path)
            if blob_client.exists():
                blob_client.delete_blob()
                return True
        except Exception:
            pass
        return False
