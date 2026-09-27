import hashlib

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_actor, get_session_id
from app.core.config import get_settings
from app.core.upload_validation import SUPPORTED_FORMATS, UploadRejected, validate_upload
from app.database.session import get_db
from app.models.file import UploadedFile
from app.reconciliation_engine.ingestion import warm_table_cache
from app.schemas.file import FileColumnsResponse, UploadedFileOut, FileMetadataResponse, SheetMetadata
from app.services.audit_service import AuditActor, record_event
from app.services.reconciliation_service import get_file_columns, get_file_metadata
from app.storage import get_storage


router = APIRouter(prefix="/files", tags=["files"])


def _sha256(stream) -> str:
    digest = hashlib.sha256()
    stream.seek(0)
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest()


@router.get("/formats")
def supported_formats() -> dict:
    """The formats RecliQ can actually parse, and the upload limits."""
    settings = get_settings()
    return {
        "formats": [{"extension": extension, **details} for extension, details in SUPPORTED_FORMATS.items()],
        "max_upload_mb": settings.max_upload_mb,
        "max_rows_per_sheet": settings.max_rows_per_sheet,
    }


@router.post("/upload", response_model=UploadedFileOut, status_code=status.HTTP_201_CREATED)
def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> UploadedFile:
    settings = get_settings()
    try:
        # Content, size and row limits are checked before anything is stored.
        validated = validate_upload(
            file.filename, file.file, max_bytes=settings.max_upload_bytes, max_rows=settings.max_rows_per_sheet
        )
    except UploadRejected as exc:
        record_event(
            scope_id=session_id, actor=actor, action="file.rejected", entity_type="file",
            summary=f"Upload rejected: {exc}", metadata={"filename": file.filename},
        )
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    checksum = _sha256(file.file)
    storage = get_storage()
    stored = storage.save_upload(file, session_id, content_type=validated.content_type)
    record = UploadedFile(
        session_id=session_id,
        original_filename=stored.original_filename,
        stored_filename=stored.stored_filename,
        storage_backend=stored.storage_backend,
        storage_path=stored.storage_path,
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    record_event(
        scope_id=session_id, actor=actor, action="file.uploaded", entity_type="file", entity_id=record.id,
        summary=f"Uploaded {record.original_filename}",
        after={
            "filename": record.original_filename,
            "size_bytes": record.size_bytes,
            "content_type": record.content_type,
            "sha256": checksum,
            "rows_per_sheet": validated.row_counts,
        },
    )
    # Parse every sheet after responding, so analysis and the run reuse it.
    background_tasks.add_task(
        warm_table_cache,
        storage.resolve_path(record.storage_path),
        record.original_filename,
    )
    return record


@router.get("/{file_id}/metadata", response_model=FileMetadataResponse)
def get_metadata(
    file_id: str,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> FileMetadataResponse:
    record = db.query(UploadedFile).filter(UploadedFile.id == file_id, UploadedFile.session_id == session_id).first()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    try:
        metadata_list = get_file_metadata(db, file_id, session_id)
    except (ValueError, ImportError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    sheets = [SheetMetadata(id=item["id"], name=item["name"]) for item in metadata_list]
    return FileMetadataResponse(file_id=file_id, filename=record.original_filename, sheets=sheets)


@router.get("/{file_id}/columns", response_model=FileColumnsResponse)
def columns(
    file_id: str,
    sheet_id: str | None = None,
    orientation: str = Query(default="vertical"),
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> FileColumnsResponse:
    try:
        columns_list = get_file_columns(db, file_id, session_id, sheet_id=sheet_id, orientation=orientation)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileColumnsResponse(file_id=file_id, sheet_id=sheet_id, orientation=orientation, columns=columns_list)
