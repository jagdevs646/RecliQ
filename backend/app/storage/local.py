from pathlib import Path
from shutil import copyfileobj
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import Settings
from app.storage.base import StoredObject


class LocalStorage:
    name = "local"

    def __init__(self, settings: Settings):
        self.base_path = Path(settings.local_storage_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def save_upload(self, file: UploadFile, session_id: str) -> StoredObject:
        suffix = Path(file.filename or "upload.xlsx").suffix or ".xlsx"
        stored_filename = f"{uuid4()}{suffix}"
        relative_path = Path("uploads") / str(session_id) / stored_filename
        destination = self.base_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        file.file.seek(0)
        with destination.open("wb") as handle:
            copyfileobj(file.file, handle)
        return StoredObject(
            original_filename=file.filename or stored_filename,
            stored_filename=stored_filename,
            storage_backend=self.name,
            storage_path=relative_path.as_posix(),
            size_bytes=destination.stat().st_size,
            content_type=file.content_type,
        )

    def save_report(self, source_path: Path, session_id: str, filename: str) -> StoredObject:
        stored_filename = f"{uuid4()}-{filename}"
        relative_path = Path("reports") / str(session_id) / stored_filename
        destination = self.base_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source_path.read_bytes())
        return StoredObject(
            original_filename=filename,
            stored_filename=stored_filename,
            storage_backend=self.name,
            storage_path=relative_path.as_posix(),
            size_bytes=destination.stat().st_size,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def resolve_path(self, storage_path: str) -> Path:
        path = self.base_path / storage_path
        if not path.exists():
            raise FileNotFoundError(storage_path)
        return path

    def delete_file(self, storage_path: str) -> bool:
        try:
            path = self.base_path / storage_path
            if path.exists():
                path.unlink(missing_ok=True)
                # Cleanup parent dir if empty
                parent = path.parent
                if parent != self.base_path and parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
                return True
        except Exception:
            pass
        return False
