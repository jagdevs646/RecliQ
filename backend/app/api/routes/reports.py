import json
import hashlib
import secrets
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_actor, get_optional_user, get_session_id
from app.core.config import get_settings
from app.database.session import get_db
from app.models.account import ReportAccessToken, ReportEmailDelivery, User
from app.models.job import ReconciliationJob
from app.models.report import Report
from app.services.audit_service import AuditActor, record_event
from app.services.policy_service import effective_limits, require_feature
from app.services.quota_service import reserve_email
from app.jobs.queue import enqueue_email_delivery
from app.storage import get_storage
from app.storage.base import report_data_manifest_path, report_data_path
from app.services.report_data_store import query_report_category, read_report_tables
from pydantic import BaseModel
from app.reconciliation_engine.universal_reporter import generate_enterprise_report


router = APIRouter(prefix="/reports", tags=["reports"])


class ReportEmailRequest(BaseModel):
    recipients: list[EmailStr] = Field(min_length=1, max_length=50)
    subject: str = Field(min_length=1, max_length=300)
    message: str = Field(default="", max_length=3000)
    include_summary: bool = True
    delivery_method: str = Field(pattern="^(attachment|secure_link)$")
    idempotency_key: str = Field(min_length=16, max_length=64)

    @field_validator("recipients")
    @classmethod
    def unique_recipients(cls, values: list[EmailStr]) -> list[EmailStr]:
        unique = list(dict.fromkeys(str(value).casefold() for value in values))
        if len(unique) != len(values):
            raise ValueError("Recipient addresses must be unique.")
        return unique


def _email_delivery_out(item: ReportEmailDelivery) -> dict[str, Any]:
    try:
        recipients = json.loads(item.recipients_json)
    except json.JSONDecodeError:
        recipients = []
    return {
        "id": item.id,
        "job_id": item.job_id,
        "report_id": item.report_id,
        "recipients": recipients,
        "subject": item.subject,
        "delivery_method": item.delivery_method,
        "status": item.status,
        "transport": item.transport,
        "error_message": item.error_message,
        "created_at": item.created_at,
        "sent_at": item.sent_at,
        "attempt_count": item.attempt_count,
    }


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
    "auto_resolved": "auto_resolved",
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


@router.get("/shared/{token}")
def download_shared_report(token: str, db: Session = Depends(get_db)) -> FileResponse:
    """A recipient-specific bearer link; only its high-entropy token is public."""
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    access = db.query(ReportAccessToken).filter(
        ReportAccessToken.token_hash == token_hash,
        ReportAccessToken.revoked_at.is_(None),
        ReportAccessToken.expires_at > now,
    ).first()
    if not access:
        raise HTTPException(status_code=404, detail="This report link is invalid, expired, or revoked.")
    delivery = db.get(ReportEmailDelivery, access.delivery_id)
    report = db.query(Report).filter(
        Report.id == delivery.report_id,
        Report.session_id == delivery.scope_id,
    ).first() if delivery and delivery.status == "sent" else None
    if not report:
        raise HTTPException(status_code=404, detail="This report link is no longer available.")
    access.accessed_at = now
    db.commit()
    record_event(
        scope_id=delivery.scope_id,
        actor=AuditActor.system("report-link"),
        action="report.link_accessed",
        entity_type="report",
        entity_id=report.id,
        summary="A recipient-specific report link was accessed",
        metadata={"delivery_id": delivery.id},
    )
    path = get_storage().resolve_path(report.storage_path)
    return FileResponse(path, filename=report.filename, media_type=_report_media_type(path))


@router.post("/job/{job_id}/email")
def email_job_report(
    job_id: str,
    payload: ReportEmailRequest,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    user: User | None = Depends(get_optional_user),
    actor: AuditActor = Depends(get_actor),
) -> list[dict[str, Any]]:
    job, report = _job_report(db, job_id, session_id)
    require_feature(db, user, "report_email")
    limits = effective_limits(db, user)
    if len(payload.recipients) > limits["max_email_recipients"]:
        raise HTTPException(status_code=429, detail={
            "code": "email_recipient_limit_exceeded",
            "message": f"This workspace can send to at most {limits['max_email_recipients']} recipient(s) per report.",
            "current_usage": len(payload.recipients),
            "allowed": limits["max_email_recipients"],
        })
    if payload.delivery_method == "attachment" and report.size_bytes > limits["max_file_size_mb"] * 1024 * 1024:
        raise HTTPException(status_code=413, detail={
            "code": "email_attachment_too_large",
            "message": "This report exceeds the workspace attachment-size limit. Choose a secure download link instead.",
            "current_bytes": report.size_bytes,
            "allowed_bytes": limits["max_file_size_mb"] * 1024 * 1024,
        })

    records: list[ReportEmailDelivery] = []
    pending: list[ReportEmailDelivery] = []
    for recipient in payload.recipients:
        recipients_json = json.dumps([str(recipient).casefold()], separators=(",", ":"))
        existing = db.query(ReportEmailDelivery).filter(
            ReportEmailDelivery.scope_id == session_id,
            ReportEmailDelivery.idempotency_key == payload.idempotency_key,
            ReportEmailDelivery.recipients_json == recipients_json,
        ).first()
        if existing:
            records.append(existing)
            continue
        delivery = ReportEmailDelivery(
            scope_id=session_id,
            job_id=job.id,
            report_id=report.id,
            sender_user_id=user.id if user else None,
            recipients_json=recipients_json,
            subject=payload.subject.strip(),
            message=payload.message.strip(),
            idempotency_key=payload.idempotency_key,
            delivery_method=payload.delivery_method,
            include_summary=payload.include_summary,
            status="queued",
            attempt_count=0,
        )
        db.add(delivery)
        records.append(delivery)
        pending.append(delivery)

    if pending:
        reserve_email(db, session_id, len(pending), limits["report_emails_per_day"])
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            resolved = [db.query(ReportEmailDelivery).filter(
                ReportEmailDelivery.scope_id == session_id,
                ReportEmailDelivery.idempotency_key == payload.idempotency_key,
                ReportEmailDelivery.recipients_json == json.dumps([str(recipient).casefold()], separators=(",", ":")),
            ).first() for recipient in payload.recipients]
            if any(item is None for item in resolved):
                raise
            records = [item for item in resolved if item is not None]
            pending = []
        else:
            for item in pending:
                db.refresh(item)
            record_event(
                scope_id=session_id,
                actor=actor,
                action="report.email_requested",
                entity_type="job",
                entity_id=job.id,
                summary=f"Requested report delivery to {len(pending)} recipient(s)",
                after={"delivery_ids": [item.id for item in pending], "method": payload.delivery_method},
            )
            for item in pending:
                try:
                    enqueue_email_delivery(item.id)
                except Exception as exc:
                    item.status = "failed"
                    item.error_message = f"The delivery could not be queued ({type(exc).__name__}). Try again later."
                    db.commit()
    for item in records:
        db.refresh(item)
    return [_email_delivery_out(item) for item in records]


@router.get("/job/{job_id}/email")
def list_job_report_emails(
    job_id: str,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> dict[str, list[dict[str, Any]]]:
    _job, _report = _job_report(db, job_id, session_id)
    deliveries = db.query(ReportEmailDelivery).filter(
        ReportEmailDelivery.scope_id == session_id,
        ReportEmailDelivery.job_id == job_id,
    ).order_by(ReportEmailDelivery.created_at.desc()).limit(50).all()
    return {"deliveries": [_email_delivery_out(item) for item in deliveries]}


@router.delete("/email-deliveries/{delivery_id}/link", status_code=status.HTTP_204_NO_CONTENT)
def revoke_report_email_link(
    delivery_id: str,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> Response:
    delivery = db.query(ReportEmailDelivery).filter(
        ReportEmailDelivery.id == delivery_id,
        ReportEmailDelivery.scope_id == session_id,
        ReportEmailDelivery.delivery_method == "secure_link",
    ).first()
    if not delivery:
        raise HTTPException(status_code=404, detail="Report email delivery not found.")
    now = datetime.now(timezone.utc)
    db.query(ReportAccessToken).filter(ReportAccessToken.delivery_id == delivery.id, ReportAccessToken.revoked_at.is_(None)).update({ReportAccessToken.revoked_at: now}, synchronize_session=False)
    db.commit()
    record_event(scope_id=session_id, actor=actor, action="report.link_revoked", entity_type="report", entity_id=delivery.report_id, summary="A report download link was revoked", metadata={"delivery_id": delivery.id})
    return Response(status_code=204)


@router.post("/email-deliveries/{delivery_id}/retry")
def retry_report_email(
    delivery_id: str,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    user: User | None = Depends(get_optional_user),
) -> dict[str, Any]:
    delivery = db.query(ReportEmailDelivery).filter(
        ReportEmailDelivery.id == delivery_id,
        ReportEmailDelivery.scope_id == session_id,
    ).first()
    if not delivery:
        raise HTTPException(status_code=404, detail="Report email delivery not found.")
    if delivery.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed deliveries can be retried.")
    require_feature(db, user, "report_email")
    limits = effective_limits(db, user)
    reserve_email(db, session_id, 1, limits["report_emails_per_day"])
    delivery.status = "queued"
    delivery.error_message = None
    db.commit()
    try:
        enqueue_email_delivery(delivery.id, attempt=delivery.attempt_count + 1)
    except Exception as exc:
        delivery.status = "failed"
        delivery.error_message = f"The delivery could not be queued ({type(exc).__name__}). Try again later."
        db.commit()
    db.refresh(delivery)
    return _email_delivery_out(delivery)


def _report_data_file(report: Report) -> Path | None:
    """Local path of the report's stored JSON data, wherever it is kept."""
    try:
        return get_storage().resolve_path(report_data_path(report.storage_path))
    except FileNotFoundError:
        return None


def _report_data_directory(report: Report) -> Path | None:
    try:
        return get_storage().resolve_path(report_data_manifest_path(report.storage_path)).parent
    except FileNotFoundError:
        return None


def _report_preview_data(report: Report | Path) -> dict[str, Any]:
    """Stored report data, from a Report record or a local workbook path."""
    if isinstance(report, Report):
        directory = _report_data_directory(report)
        if directory is not None:
            try:
                return read_report_tables(directory)
            except (OSError, ValueError, KeyError) as exc:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Report table data is invalid") from exc
    raw_path = report.with_name(f"{report.stem}_data.json") if isinstance(report, Path) else _report_data_file(report)
    if raw_path is None or not raw_path.exists():
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
    include_auto_resolved: bool = True
    date_format: str = "YYYY-MM-DD"
    number_format: str = "#,##0.00"


@router.get("/{report_id}/download")
def download_report(
    report_id: str,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> FileResponse:
    report = db.query(Report).filter(Report.id == report_id, Report.session_id == session_id).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    path = get_storage().resolve_path(report.storage_path)
    record_event(
        scope_id=session_id, actor=actor, action="report.downloaded", entity_type="report", entity_id=report.id,
        summary=f"Downloaded {report.filename}",
    )
    return FileResponse(path, filename=report.filename, media_type=_report_media_type(path))


@router.get("/job/{job_id}/download", response_model=None)
def download_job_report(
    job_id: str,
    file_pair_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> FileResponse | Response:
    _, report = _job_report(db, job_id, session_id)
    path = get_storage().resolve_path(report.storage_path)
    record_event(
        scope_id=session_id, actor=actor, action="report.downloaded", entity_type="job", entity_id=job_id,
        summary=f"Downloaded report {report.filename}", metadata={"file_pair_id": file_pair_id, "report_id": report.id},
    )
    if not file_pair_id or path.suffix.lower() != ".zip":
        return FileResponse(path, filename=report.filename, media_type=_report_media_type(path))

    try:
        manifest = json.loads(report.summary_json or "{}").get("file_pairs", [])
    except json.JSONDecodeError:
        manifest = []
    if len(manifest) == 1:
        if manifest[0].get("file_pair_id") != file_pair_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File pair not found in this job")
        # A capped single-pair report is a ZIP of one workbook and its full CSVs.
        return FileResponse(path, filename=report.filename, media_type="application/zip")
    pair = next((item for item in manifest if item.get("file_pair_id") == file_pair_id), None)
    member = pair.get("report_filename") if pair else None
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No report was generated for this file pair")
    with zipfile.ZipFile(path) as archive:
        if member not in archive.namelist():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File pair report not found in archive")
        import os
        import shutil
        import tempfile

        fd, temp_name = tempfile.mkstemp(suffix=Path(member).suffix, prefix="recliq_pair_report_")
        os.close(fd)
        extracted = Path(temp_name)
        try:
            with archive.open(member) as source, extracted.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
        except Exception:
            extracted.unlink(missing_ok=True)
            raise
    from starlette.background import BackgroundTask

    return FileResponse(
        extracted,
        filename=member,
        media_type=_report_media_type(extracted),
        background=BackgroundTask(lambda: extracted.unlink(missing_ok=True)),
    )


_PAIR_RECORD_CATEGORIES = ("exceptions", "matched_records", "missing_in_file_1", "missing_in_file_2", "identity_resolution", "auto_resolved")


def _file_pair_universal_data(universal_data: dict[str, Any], file_pair_id: str) -> dict[str, Any]:
    """One file pair's report data, cut from a multi-pair job's merged data."""
    pair_report = (universal_data.get("file_pair_reports") or {}).get(file_pair_id)
    if pair_report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File pair not found in this job")
    return {
        **pair_report,
        **{
            category: (
                universal_data[category].for_file_pair(file_pair_id)
                if hasattr(universal_data.get(category), "for_file_pair")
                else [record for record in universal_data.get(category, []) if record.get("File Pair ID") == file_pair_id]
            )
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
    actor: AuditActor = Depends(get_actor),
) -> FileResponse:
    _, report = _job_report(db, job_id, session_id)
    storage = get_storage()
    path = storage.resolve_path(report.storage_path)
    raw_path = _report_data_file(report) or path.with_name(f"{path.stem}_data.json")
    table_directory = _report_data_directory(report)
    is_archive = path.suffix.lower() == ".zip"
    try:
        file_pairs = json.loads(report.summary_json or "{}").get("file_pairs", [])
    except json.JSONDecodeError:
        file_pairs = []
    if file_pair_id:
        selected_pair = next((item for item in file_pairs if item.get("file_pair_id") == file_pair_id), None)
        if selected_pair is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File pair not found in this job")
    elif len(file_pairs) > 1:
        raise HTTPException(
            status_code=422,
            detail="Choose a file pair to customize: each file pair has its own workbook.",
        )
    else:
        selected_pair = file_pairs[0] if file_pairs else None
        file_pair_id = selected_pair.get("file_pair_id") if selected_pair else None
    record_event(
        scope_id=session_id, actor=actor, action="report.customized", entity_type="job", entity_id=job_id,
        summary="Downloaded a customized report", after=config.model_dump(), metadata={"file_pair_id": file_pair_id},
    )

    if table_directory is not None:
        try:
            universal_data = read_report_tables(
                table_directory,
                file_pair_id=file_pair_id if is_archive and len(file_pairs) > 1 else None,
            )
        except (OSError, ValueError, KeyError) as exc:
            if isinstance(exc, KeyError):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File pair not found in this job") from exc
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Report table data is invalid") from exc
    elif not raw_path.exists():
        if is_archive:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report data is unavailable for customization")
        # Fallback to existing Excel file if raw data is lost
        return FileResponse(path, filename=report.filename, media_type=_report_media_type(path))
    else:
        with open(raw_path, "r", encoding="utf-8") as f:
            universal_data = json.load(f)
    download_name = report.filename
    if is_archive and table_directory is None and len(file_pairs) > 1:
        universal_data = _file_pair_universal_data(universal_data, file_pair_id)
        download_name = universal_data.get("report_filename") or "Reconciliation.xlsx"
    elif is_archive and selected_pair:
        download_name = selected_pair.get("report_filename") or report.filename
    elif is_archive and table_directory is not None:
        download_name = universal_data.get("report_filename") or "Reconciliation.xlsx"

    # Generate new temp file
    import tempfile
    
    fd, temp_path_str = tempfile.mkstemp(suffix=".xlsx", prefix="recliq_custom_")
    import os
    os.close(fd)
    temp_path = Path(temp_path_str)
    
    try:
        generated = generate_enterprise_report(universal_data, config.model_dump(), temp_path)
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        raise HTTPException(status_code=500, detail=str(e))
        
    # We will let FileResponse handle it and optionally delete after? Wait, FileResponse doesn't delete automatically.
    # FastAPI background task can delete it.
    if generated.capped:
        archive_path = temp_path.with_suffix(".zip")
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(temp_path, temp_path.name)
            for csv_path in generated.csv_paths:
                archive.write(csv_path, csv_path.name)
        temp_path.unlink(missing_ok=True)
        for csv_path in generated.csv_paths:
            csv_path.unlink(missing_ok=True)
        temp_path = archive_path
    from starlette.background import BackgroundTask
    return FileResponse(
        temp_path, 
        filename=f"Custom_{Path(download_name).stem}{'.zip' if temp_path.suffix == '.zip' else '.xlsx'}",
        media_type="application/zip" if temp_path.suffix == ".zip" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
    search: str = Query(default="", max_length=200),
    sort_by: str | None = Query(default=None, max_length=200),
    sort_direction: str = Query(default="asc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> dict[str, Any]:
    _, report = _job_report(db, job_id, session_id)
    section = _PREVIEW_SECTIONS.get(category)
    if section is None:
        raise HTTPException(status_code=422, detail="Unknown preview category")
    table_directory = _report_data_directory(report)
    if table_directory is not None:
        try:
            queried = query_report_category(
                table_directory, section, offset=offset, limit=limit,
                file_pair_id=file_pair_id, sheet_rule_id=sheet_rule_id,
                classifications=_PREVIEW_CLASSIFICATIONS.get(category),
                exclude_exact_matches=category == "review", search=search,
                sort_by=sort_by, sort_direction=sort_direction,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (OSError, KeyError) as exc:
            raise HTTPException(status_code=500, detail="Report preview data is unavailable") from exc
        columns = [column for column in queried["columns"] if _preview_column_visible(column)]
        page = queried["rows"]
        total_rows = queried["total_rows"]
    else:
        data = _report_preview_data(report)
        records = data.get(section, [])
        if file_pair_id:
            records = [record for record in records if record.get("File Pair ID") == file_pair_id]
        if sheet_rule_id:
            records = [record for record in records if record.get("Sheet Rule ID") == sheet_rule_id]
        if category == "review":
            records = [record for record in records if record.get("IDENTITY CLASSIFICATION") != "EXACT_MATCH"]
        elif category in _PREVIEW_CLASSIFICATIONS:
            records = [record for record in records if record.get("IDENTITY CLASSIFICATION") in _PREVIEW_CLASSIFICATIONS[category]]
        if search.strip():
            needle = search.strip().casefold()
            records = [record for record in records if any(needle in str(value).casefold() for key, value in record.items() if _preview_column_visible(str(key)) and value is not None)]
        if sort_by:
            if any(sort_by not in record for record in records):
                raise HTTPException(status_code=422, detail="The selected sort column is not available in this result.")
            records = sorted(records, key=lambda item: (item.get(sort_by) is None, str(item.get(sort_by) or "").casefold()), reverse=sort_direction == "desc")
        columns = [column for column in dict.fromkeys(str(column) for record in records for column in record) if _preview_column_visible(column)]
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
