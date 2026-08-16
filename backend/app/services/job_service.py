from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.models.file import UploadedFile
from app.models.history import ReconciliationHistory
from app.models.job import ReconciliationJob
from app.models.report import Report


def list_jobs(db: Session, session_id: str, limit: int = 50) -> list[ReconciliationJob]:
    return (
        db.query(ReconciliationJob)
        .filter(ReconciliationJob.session_id == session_id)
        .order_by(ReconciliationJob.created_at.desc())
        .limit(limit)
        .all()
    )


def get_job(db: Session, job_id: str, session_id: str) -> ReconciliationJob | None:
    return db.query(ReconciliationJob).filter(ReconciliationJob.id == job_id, ReconciliationJob.session_id == session_id).first()


def append_history(db: Session, job: ReconciliationJob, event_type: str, message: str, metadata_json: str = "{}") -> None:
    db.add(
        ReconciliationHistory(
            session_id=job.session_id,
            job_id=job.id,
            event_type=event_type,
            message=message,
            metadata_json=metadata_json,
        )
    )


def cancel_job(db: Session, job_id: str, session_id: str) -> ReconciliationJob | None:
    job = get_job(db, job_id, session_id)
    if not job:
        return None
    if job.status in {"queued", "processing"}:
        job.status = "cancelled"
        job.completed_at = datetime.now(timezone.utc)
        job.error_message = "Reconciliation cancelled by user"
        append_history(db, job, "cancelled", "Reconciliation cancelled by user")
        db.commit()
        db.refresh(job)
    return job


def delete_job(db: Session, job_id: str, session_id: str, storage=None) -> bool:
    job = get_job(db, job_id, session_id)
    if not job:
        return False

    report_id = job.report_id
    file_1_id = job.input_file_1_id
    file_2_id = job.input_file_2_id

    # Delete history
    db.query(ReconciliationHistory).filter(ReconciliationHistory.job_id == job.id).delete(synchronize_session=False)

    # Delete job record first to detach FK relationships
    db.delete(job)
    db.flush()

    # Clean up associated report
    if report_id:
        report = db.query(Report).filter(Report.id == report_id).first()
        if report:
            if storage:
                try:
                    storage.delete_file(report.storage_path)
                except Exception:
                    pass
            db.delete(report)

    # Clean up uploaded files if no other job references them
    for file_id in (file_1_id, file_2_id):
        if file_id:
            other_refs = (
                db.query(ReconciliationJob)
                .filter(
                    (ReconciliationJob.input_file_1_id == file_id)
                    | (ReconciliationJob.input_file_2_id == file_id)
                )
                .count()
            )
            if other_refs == 0:
                file_rec = db.query(UploadedFile).filter(UploadedFile.id == file_id).first()
                if file_rec:
                    if storage:
                        try:
                            storage.delete_file(file_rec.storage_path)
                        except Exception:
                            pass
                    db.delete(file_rec)

    db.commit()
    return True


def clear_all_jobs(db: Session, session_id: str, storage=None) -> int:
    jobs = (
        db.query(ReconciliationJob)
        .filter(ReconciliationJob.session_id == session_id)
        .all()
    )
    count = 0
    for job in jobs:
        if delete_job(db, job.id, session_id, storage):
            count += 1
    return count


def prune_old_jobs(db: Session, session_id: str, storage=None, max_records: int = 20) -> int:
    """
    Ensure a maximum of max_records (default 20) reconciliation records exist per session.
    Removes oldest excess records and cleans up storage files.
    """
    jobs = (
        db.query(ReconciliationJob)
        .filter(ReconciliationJob.session_id == session_id)
        .order_by(ReconciliationJob.created_at.desc())
        .all()
    )
    if len(jobs) <= max_records:
        return 0

    excess_jobs = jobs[max_records:]
    pruned_count = 0
    for job in excess_jobs:
        if delete_job(db, job.id, session_id, storage):
            pruned_count += 1
    return pruned_count
