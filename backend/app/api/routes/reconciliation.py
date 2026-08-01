import tempfile
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_session_id
from app.database.session import get_db
from app.models.job import ReconciliationJob
from app.reconciliation.gst import AMOUNT_COLUMNS, MERGE_KEY_COLUMNS, REQUIRED_COLUMNS
from app.reconciliation.gst import generate_sample_format as generate_gst_sample
from app.reconciliation_engine.report_generator import generate_generic_sample_format
from app.schemas.job import ReconciliationJobOut
from app.schemas.reconciliation import GenericReconciliationRequest, GSTReconciliationRequest
from app.services.reconciliation_service import enqueue_generic_job, enqueue_gst_job


router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.get("/gst/config")
def gst_configuration() -> dict[str, list[str]]:
    """Expose the GST engine contract so every client follows the same workflow."""
    return {
        "required_columns": REQUIRED_COLUMNS,
        "matching_fields": ["GSTR", "INVOICE NO."],
        "grouping_fields": MERGE_KEY_COLUMNS,
        "amount_fields": AMOUNT_COLUMNS,
    }


@router.get("/sample-template")
def download_sample_template(type: str = Query("generic")) -> FileResponse:
    """Generate and serve a sample Excel workbook structure for generic or GST reconciliation."""
    is_gst = type.lower() == "gst"
    filename = "RecliQ_GST_Sample_Template.xlsx" if is_gst else "RecliQ_General_Sample_Template.xlsx"
    temp_dir = Path(tempfile.gettempdir())
    sample_path = temp_dir / filename

    if is_gst:
        generate_gst_sample(sample_path)
    else:
        generate_generic_sample_format(sample_path)

    return FileResponse(
        path=sample_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )


@router.post("/generic", response_model=ReconciliationJobOut)
def start_generic_reconciliation(
    payload: GenericReconciliationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> ReconciliationJob:
    return enqueue_generic_job(db, payload, background_tasks, session_id)


@router.post("/gst", response_model=ReconciliationJobOut)
def start_gst_reconciliation(
    payload: GSTReconciliationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> ReconciliationJob:
    return enqueue_gst_job(db, payload, background_tasks, session_id)
