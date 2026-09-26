import json
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.api.deps import get_session_id
from app.database.session import get_db
from app.models.job import ReconciliationJob
from app.models.report import Report
from app.storage import get_storage
from pydantic import BaseModel
from app.reconciliation_engine.universal_reporter import generate_enterprise_report


router = APIRouter(prefix="/reports", tags=["reports"])


def _job_report(db: Session, job_id: str, session_id: str) -> tuple[ReconciliationJob, Report]:
    job = db.query(ReconciliationJob).filter(ReconciliationJob.id == job_id, ReconciliationJob.session_id == session_id).first()
    if not job or not job.report_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not available")
    report = db.query(Report).filter(Report.id == job.report_id, Report.session_id == session_id).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return job, report


_PREVIEW_SECTIONS = {
    "discrepancies": "exceptions",
    "only_file_1": "missing_in_file_2",
    "only_file_2": "missing_in_file_1",
    "review": "identity_resolution",
    "exception_matches": "identity_resolution",
    "ambiguous_matches": "identity_resolution",
    "not_found": "identity_resolution",
}

# Identity-scoped categories select audit rows by their recorded classification.
_PREVIEW_CLASSIFICATIONS = {
    "exception_matches": {"EXCEPTION_MATCH"},
    "ambiguous_matches": {"AMBIGUOUS_MATCH"},
    "not_found": {"NOT_FOUND"},
}

# Scope identifiers and normalized match forms are engine internals, not data.
_HIDDEN_PREVIEW_COLUMNS = {"File Pair ID", "Sheet Rule ID"}
_HIDDEN_PREVIEW_PREFIXES = ("NORM_", "MATCHED NORM_", "__")


def _preview_column_visible(column: str) -> bool:
    return column not in _HIDDEN_PREVIEW_COLUMNS and not column.startswith(_HIDDEN_PREVIEW_PREFIXES)


def _scoped_summary(summary: dict[str, Any], file_pair_id: str | None, sheet_rule_id: str | None) -> dict[str, Any]:
    """Return one file pair's or sheet rule's own counts from the job manifest."""
    if sheet_rule_id:
        rule = next((item for item in summary.get("sheet_rules", []) if item.get("sheet_rule_id") == sheet_rule_id), None)
        if rule is None or (file_pair_id and rule.get("file_pair_id") != file_pair_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sheet rule not found in this job")
        return {**rule.get("summary", {}), "scope": {"file_pair_id": rule.get("file_pair_id"), "sheet_rule_id": sheet_rule_id}, "status": rule.get("status"), "error": rule.get("error")}
    if file_pair_id:
        pair = next((item for item in summary.get("file_pairs", []) if item.get("file_pair_id") == file_pair_id), None)
        if pair is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File pair not found in this job")
        return {**pair.get("summary", {}), "scope": {"file_pair_id": file_pair_id}, "status": pair.get("status")}
    return summary


def _report_media_type(path: Path) -> str:
    return "application/zip" if path.suffix.lower() == ".zip" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _report_preview_data(path: Path) -> dict[str, Any]:
    raw_path = path.with_name(f"{path.stem}_data.json")
    if not raw_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report preview metadata is unavailable")
    try:
        with open(raw_path, encoding="utf-8") as raw_file:
            return json.load(raw_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Report preview metadata is invalid") from exc



def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value

class ReportCustomConfig(BaseModel):
    include_summary: bool = True
    include_exceptions: bool = True
    include_matched: bool = True
    include_missing_file_1: bool = True
    include_missing_file_2: bool = True
    include_field_differences: bool = True
    include_controls: bool = True
    date_format: str = "YYYY-MM-DD"
    number_format: str = "#,##0.00"


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
    return FileResponse(path, filename=report.filename, media_type=_report_media_type(path))


@router.get("/job/{job_id}/download", response_model=None)
def download_job_report(
    job_id: str,
    file_pair_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> FileResponse | Response:
    _, report = _job_report(db, job_id, session_id)
    path = get_storage().resolve_path(report.storage_path)
    if not file_pair_id or path.suffix.lower() != ".zip":
        # A single-pair job's only workbook is the whole report.
        return FileResponse(path, filename=report.filename, media_type=_report_media_type(path))

    try:
        manifest = json.loads(report.summary_json or "{}").get("file_pairs", [])
    except json.JSONDecodeError:
        manifest = []
    pair = next((item for item in manifest if item.get("file_pair_id") == file_pair_id), None)
    member = pair.get("report_filename") if pair else None
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No report was generated for this file pair")
    with zipfile.ZipFile(path) as archive:
        if member not in archive.namelist():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File pair report not found in archive")
        content = archive.read(member)
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{member}"'},
    )


_PAIR_RECORD_CATEGORIES = ("exceptions", "matched_records", "missing_in_file_1", "missing_in_file_2", "identity_resolution")


def _file_pair_universal_data(universal_data: dict[str, Any], file_pair_id: str) -> dict[str, Any]:
    """One file pair's report data, cut from a multi-pair job's merged data."""
    pair_report = (universal_data.get("file_pair_reports") or {}).get(file_pair_id)
    if pair_report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File pair not found in this job")
    return {
        **pair_report,
        **{
            category: [record for record in universal_data.get(category, []) if record.get("File Pair ID") == file_pair_id]
            for category in _PAIR_RECORD_CATEGORIES
        },
    }


@router.post("/job/{job_id}/download_custom")
def download_custom_report(
    job_id: str,
    config: ReportCustomConfig,
    file_pair_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> FileResponse:
    _, report = _job_report(db, job_id, session_id)
    storage = get_storage()
    path = storage.resolve_path(report.storage_path)
    raw_path = path.with_name(f"{path.stem}_data.json")
    is_archive = path.suffix.lower() == ".zip"

    if is_archive and not file_pair_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Choose a file pair to customize: each file pair has its own workbook.",
        )

    if not raw_path.exists():
        if is_archive:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report data is unavailable for customization")
        # Fallback to existing Excel file if raw data is lost
        return FileResponse(path, filename=report.filename, media_type=_report_media_type(path))

    with open(raw_path, "r", encoding="utf-8") as f:
        universal_data = json.load(f)
    download_name = report.filename
    if is_archive:
        universal_data = _file_pair_universal_data(universal_data, file_pair_id)
        download_name = universal_data.get("report_filename") or "Reconciliation.xlsx"

    # Generate new temp file
    import tempfile
    
    fd, temp_path_str = tempfile.mkstemp(suffix=".xlsx", prefix="recliq_custom_")
    import os
    os.close(fd)
    temp_path = Path(temp_path_str)
    
    try:
        generate_enterprise_report(universal_data, config.model_dump(), temp_path)
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        raise HTTPException(status_code=500, detail=str(e))
        
    # We will let FileResponse handle it and optionally delete after? Wait, FileResponse doesn't delete automatically.
    # FastAPI background task can delete it.
    from starlette.background import BackgroundTask
    return FileResponse(
        temp_path, 
        filename=f"Custom_{download_name}",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        background=BackgroundTask(lambda: temp_path.unlink(missing_ok=True) if temp_path.exists() else None)
    )


@router.get("/job/{job_id}/summary")
def job_report_summary(
    job_id: str,
    file_pair_id: str | None = Query(default=None),
    sheet_rule_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> dict[str, Any]:
    _, report = _job_report(db, job_id, session_id)
    try:
        summary = json.loads(report.summary_json or "{}")
    except json.JSONDecodeError:
        summary = {}
    return _scoped_summary(summary, file_pair_id, sheet_rule_id)


@router.get("/job/{job_id}/preview")
def job_report_preview(
    job_id: str,
    category: str = Query(default="discrepancies"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=25),
    file_pair_id: str | None = Query(default=None),
    sheet_rule_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> dict[str, Any]:
    _, report = _job_report(db, job_id, session_id)
    path = get_storage().resolve_path(report.storage_path)
    section = _PREVIEW_SECTIONS.get(category)
    if section is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown preview category")
    data = _report_preview_data(path)
    records = data.get(section, [])
    if file_pair_id:
        records = [record for record in records if record.get("File Pair ID") == file_pair_id]
    if sheet_rule_id:
        records = [record for record in records if record.get("Sheet Rule ID") == sheet_rule_id]
    if category == "review":
        records = [record for record in records if record.get("IDENTITY CLASSIFICATION") != "EXACT_MATCH"]
    elif category in _PREVIEW_CLASSIFICATIONS:
        records = [record for record in records if record.get("IDENTITY CLASSIFICATION") in _PREVIEW_CLASSIFICATIONS[category]]
    columns = [
        column
        for column in dict.fromkeys(str(column) for record in records for column in record)
        if _preview_column_visible(column)
    ]
    total_rows = len(records)
    page = records[offset:offset + limit]
    return {
        "category": category,
        "sheet_name": section,
        "columns": columns,
        "rows": [
            {str(column): _json_value(value) for column, value in record.items() if _preview_column_visible(str(column))}
            for record in page
        ],
        "total_rows": total_rows,
        "offset": offset,
        "limit": limit,
    }
