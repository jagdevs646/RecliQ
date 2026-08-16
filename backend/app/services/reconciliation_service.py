from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import SessionLocal
from app.models.file import UploadedFile
from app.models.job import ReconciliationJob
from app.models.report import Report
from app.reconciliation_engine.background_jobs import run_in_background
from app.reconciliation_engine.engine import (
    read_excel_columns,
    run_generic_reconciliation,
    run_gst_reconciliation,
)
from app.schemas.reconciliation import GenericReconciliationRequest, GSTReconciliationRequest
from app.services.job_service import append_history
from app.storage import get_storage


def _file_record(db: Session, file_id: str, session_id: str) -> UploadedFile:
    record = db.query(UploadedFile).filter(UploadedFile.id == file_id, UploadedFile.session_id == session_id).first()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File not found: {file_id}")
    return record


def enqueue_generic_job(
    db: Session,
    payload: GenericReconciliationRequest,
    background_tasks: BackgroundTasks,
    session_id: str,
) -> ReconciliationJob:
    _file_record(db, payload.file_1_id, session_id)
    _file_record(db, payload.file_2_id, session_id)
    job = ReconciliationJob(
        session_id=session_id,
        job_type="generic",
        status="queued",
        progress=0,
        orientation=payload.orientation,
        input_file_1_id=payload.file_1_id,
        input_file_2_id=payload.file_2_id,
        settings_json=payload.model_dump_json(),
    )
    db.add(job)
    db.flush()
    append_history(db, job, "queued", "Generic reconciliation job queued")
    db.commit()
    db.refresh(job)
    background_tasks.add_task(process_reconciliation_job_async, job.id)
    return job


def enqueue_gst_job(
    db: Session,
    payload: GSTReconciliationRequest,
    background_tasks: BackgroundTasks,
    session_id: str,
) -> ReconciliationJob:
    _file_record(db, payload.file_1_id, session_id)
    _file_record(db, payload.file_2_id, session_id)
    job = ReconciliationJob(
        session_id=session_id,
        job_type="gst",
        status="queued",
        progress=0,
        orientation=payload.orientation,
        input_file_1_id=payload.file_1_id,
        input_file_2_id=payload.file_2_id,
        settings_json=payload.model_dump_json(),
    )
    db.add(job)
    db.flush()
    append_history(db, job, "queued", "GST reconciliation job queued")
    db.commit()
    db.refresh(job)
    background_tasks.add_task(process_reconciliation_job_async, job.id)
    return job


def get_file_columns(db: Session, file_id: str, session_id: str, orientation: str = "vertical") -> list[str]:
    record = _file_record(db, file_id, session_id)
    path = get_storage().resolve_path(record.storage_path)
    return read_excel_columns(path, orientation=orientation)


def process_reconciliation_job_async(job_id: str) -> None:
    # FastAPI already runs sync background tasks in a thread off the event loop.
    # Calling directly avoids double-threading (submit + future.result()) which
    # caused hangs with multi-worker Uvicorn deployments.
    process_reconciliation_job(job_id)


def process_reconciliation_job(job_id: str) -> None:
    db = SessionLocal()
    settings = get_settings()
    storage = get_storage()
    file_1_path: Path | None = None
    file_2_path: Path | None = None
    output_path: Path | None = None
    try:
        job = db.get(ReconciliationJob, job_id)
        if not job:
            return

        if job.status == "cancelled":
            return

        job.status = "processing"
        job.progress = 10
        job.started_at = datetime.now(timezone.utc)
        append_history(db, job, "processing", "Reconciliation started")
        db.commit()

        def is_cancelled() -> bool:
            try:
                check_db = SessionLocal()
                j = check_db.get(ReconciliationJob, job_id)
                cancelled = j is not None and j.status == "cancelled"
                check_db.close()
                return cancelled
            except Exception:
                return False

        def on_progress(percent: int, step_msg: str) -> None:
            nonlocal db, job_id
            try:
                sub_db = SessionLocal()
                j = sub_db.get(ReconciliationJob, job_id)
                if j and j.status != "cancelled":
                    j.progress = percent
                    append_history(sub_db, j, "processing", step_msg)
                    sub_db.commit()
                sub_db.close()
            except Exception:
                pass

        file_1 = (
            db.query(UploadedFile)
            .filter(UploadedFile.id == job.input_file_1_id, UploadedFile.session_id == job.session_id)
            .first()
        )
        file_2 = (
            db.query(UploadedFile)
            .filter(UploadedFile.id == job.input_file_2_id, UploadedFile.session_id == job.session_id)
            .first()
        )
        if not file_1 or not file_2:
            raise RuntimeError("One or both source files are missing")

        file_1_path = storage.resolve_path(file_1.storage_path)
        file_2_path = storage.resolve_path(file_2.storage_path)
        work_dir = Path(settings.local_storage_path) / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        output_name = "GST_Reconciliation.xlsx" if job.job_type == "gst" else "Reconciliation.xlsx"
        output_path = work_dir / f"{job.id}-{output_name}"
        payload = json.loads(job.settings_json or "{}")

        if is_cancelled():
            raise InterruptedError("Reconciliation cancelled by user")

        if job.job_type == "gst":
            summary = run_gst_reconciliation(
                file_1_path,
                file_2_path,
                output_path,
                orientation=payload.get("orientation", job.orientation),
                text_threshold=int(payload.get("text_threshold", 85)),
                progress_callback=on_progress,
                file_1_name=file_1.original_filename,
                file_2_name=file_2.original_filename,
                is_cancelled=is_cancelled,
            )
        else:
            summary = run_generic_reconciliation(
                file_1_path,
                file_2_path,
                output_path,
                key_file_1=payload["key_file_1"],
                key_file_2=payload["key_file_2"],
                rules=payload.get("rules", []),
                orientation=payload.get("orientation", job.orientation),
                include_columns_file_1=payload.get("include_columns_file_1", []),
                include_columns_file_2=payload.get("include_columns_file_2", []),
                progress_callback=on_progress,
                file_1_name=file_1.original_filename,
                file_2_name=file_2.original_filename,
                is_cancelled=is_cancelled,
            )

        if is_cancelled():
            raise InterruptedError("Reconciliation cancelled by user")

        stored_report = storage.save_report(output_path, job.session_id, output_name)
        report = Report(
            session_id=job.session_id,
            filename=output_name,
            storage_backend=stored_report.storage_backend,
            storage_path=stored_report.storage_path,
            size_bytes=stored_report.size_bytes,
            summary_json=json.dumps(summary),
        )
        db.add(report)
        db.flush()

        job.report_id = report.id
        job.status = "completed"
        job.progress = 100
        job.completed_at = datetime.now(timezone.utc)
        append_history(db, job, "completed", "Reconciliation completed", json.dumps(summary))
        db.commit()

        # Enforce maximum 20 stored records per session
        from app.services.job_service import prune_old_jobs
        try:
            prune_old_jobs(db, job.session_id, storage, max_records=20)
        except Exception:
            pass
    except InterruptedError:
        # User requested cancellation during processing
        try:
            db.rollback()
        except Exception:
            pass
        job = db.get(ReconciliationJob, job_id)
        if job and job.status != "cancelled":
            job.status = "cancelled"
            job.completed_at = datetime.now(timezone.utc)
            job.error_message = "Reconciliation cancelled by user"
            append_history(db, job, "cancelled", "Reconciliation cancelled by user")
            try:
                db.commit()
            except Exception:
                pass
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        job = db.get(ReconciliationJob, job_id)
        if job and job.status != "cancelled":
            job.status = "failed"
            job.progress = 100
            job.error_message = str(exc)
            job.completed_at = datetime.now(timezone.utc)
            append_history(db, job, "failed", str(exc))
            try:
                db.commit()
            except Exception:
                pass
    finally:
        db.close()
        try:
            if file_1_path and file_1_path.exists():
                file_1_path.unlink(missing_ok=True)
            if file_2_path and file_2_path.exists():
                file_2_path.unlink(missing_ok=True)
            if output_path and output_path.exists():
                output_path.unlink(missing_ok=True)
        except Exception:
            pass
