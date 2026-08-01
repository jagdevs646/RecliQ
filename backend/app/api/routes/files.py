from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_session_id
from app.database.session import get_db
from app.models.file import UploadedFile
from app.schemas.file import FileColumnsResponse, UploadedFileOut
from app.services.reconciliation_service import get_file_columns
from app.storage import get_storage
from app.utils.excel import is_supported_workbook


router = APIRouter(prefix="/files", tags=["files"])


@router.post("/upload", response_model=UploadedFileOut, status_code=status.HTTP_201_CREATED)
def upload_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> UploadedFile:
    if not is_supported_workbook(file.filename):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only Excel workbooks are supported")
    stored = get_storage().save_upload(file, session_id)
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
    return record


@router.get("/{file_id}/columns", response_model=FileColumnsResponse)
def columns(
    file_id: str,
    orientation: str = Query(default="vertical"),
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> FileColumnsResponse:
    columns_list = get_file_columns(db, file_id, session_id, orientation=orientation)
    return FileColumnsResponse(file_id=file_id, orientation=orientation, columns=columns_list)
