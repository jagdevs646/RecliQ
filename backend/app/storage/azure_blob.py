from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import Settings
from app.storage.base import StoredObject


class AzureBlobStorage:
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

    def save_upload(self, file: UploadFile, session_id: str) -> StoredObject:
        suffix = Path(file.filename or "upload.xlsx").suffix or ".xlsx"
        stored_filename = f"{uuid4()}{suffix}"
        storage_path = f"uploads/{str(session_id)}/{stored_filename}"
        file.file.seek(0)
        data = file.file.read()
        self.container.upload_blob(storage_path, data, overwrite=True)
        return StoredObject(
            original_filename=file.filename or stored_filename,
            stored_filename=stored_filename,
            storage_backend=self.name,
            storage_path=storage_path,
            size_bytes=len(data),
            content_type=file.content_type,
        )

    def save_report(self, source_path: Path, session_id: str, filename: str) -> StoredObject:
        stored_filename = f"{uuid4()}-{filename}"
        storage_path = f"reports/{str(session_id)}/{stored_filename}"
        data = source_path.read_bytes()
        self.container.upload_blob(storage_path, data, overwrite=True)
        return StoredObject(
            original_filename=filename,
            stored_filename=stored_filename,
            storage_backend=self.name,
            storage_path=storage_path,
            size_bytes=len(data),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def resolve_path(self, storage_path: str) -> Path:
        temp_path = Path(gettempdir()) / f"reconx-{uuid4()}-{Path(storage_path).name}"
        data = self.container.download_blob(storage_path).readall()
        temp_path.write_bytes(data)
        return temp_path

    def delete_file(self, storage_path: str) -> bool:
        try:
            blob_client = self.container.get_blob_client(storage_path)
            if blob_client.exists():
                blob_client.delete_blob()
                return True
        except Exception:
            pass
        return False
