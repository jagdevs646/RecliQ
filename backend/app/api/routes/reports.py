import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_session_id
from app.database.session import get_db
from app.models.job import ReconciliationJob
from app.models.report import Report
from app.storage import get_storage


router = APIRouter(prefix="/reports", tags=["reports"])


def _job_report(db: Session, job_id: str, session_id: str) -> tuple[ReconciliationJob, Report]:
    job = db.query(ReconciliationJob).filter(ReconciliationJob.id == job_id, ReconciliationJob.session_id == session_id).first()
    if not job or not job.report_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not available")
    report = db.query(Report).filter(Report.id == job.report_id, Report.session_id == session_id).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return job, report


def _preview_sheet_name(path: Path, category: str) -> str:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        names = workbook.sheetnames
    finally:
        workbook.close()

    index_by_category = {"discrepancies": 0, "only_file_1": 1, "only_file_2": 2, "review": 3}
    if category not in index_by_category or index_by_category[category] >= len(names):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported preview category")
    return names[index_by_category[category]]


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


@router.get("/{report_id}/download")
def download_report(
    report_id: str,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> FileResponse:
    report = db.query(Report).filter(Report.id == report_id, Report.session_id == session_id).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    path = get_storage().resolve_path(report.storage_path)
    return FileResponse(path, filename=report.filename, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/job/{job_id}/download")
def download_job_report(
    job_id: str,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> FileResponse:
    _, report = _job_report(db, job_id, session_id)
    path = get_storage().resolve_path(report.storage_path)
    return FileResponse(path, filename=report.filename, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/job/{job_id}/summary")
def job_report_summary(job_id: str, db: Session = Depends(get_db), session_id: str = Depends(get_session_id)) -> dict[str, Any]:
    _, report = _job_report(db, job_id, session_id)
    try:
        return json.loads(report.summary_json or "{}")
    except json.JSONDecodeError:
        return {}


@router.get("/job/{job_id}/preview")
def job_report_preview(
    job_id: str,
    category: str = Query(default="discrepancies"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=25),
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> dict[str, Any]:
    _, report = _job_report(db, job_id, session_id)
    path = get_storage().resolve_path(report.storage_path)
    sheet_name = _preview_sheet_name(path, category)
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        total_rows = max(0, workbook[sheet_name].max_row - 1)
    finally:
        workbook.close()
    frame = pd.read_excel(path, sheet_name=sheet_name, skiprows=range(1, offset + 1), nrows=limit)
    records = [
        {str(column): _json_value(value) for column, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]
    return {
        "category": category,
        "sheet_name": sheet_name,
        "columns": [str(column) for column in frame.columns],
        "rows": records,
        "total_rows": total_rows,
        "offset": offset,
        "limit": limit,
    }
