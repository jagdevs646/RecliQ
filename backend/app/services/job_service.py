import json
from datetime import date, datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import or_
from sqlalchemy.orm import aliased
from sqlalchemy.orm import Session

from app.models.account import User
from app.models.file import UploadedFile
from app.models.history import ReconciliationHistory
from app.models.job import ReconciliationJob
from app.models.report import Report
from app.schemas.reconciliation import ReconciliationPlan


def list_jobs(db: Session, session_id: str, limit: int = 50) -> list[ReconciliationJob]:
    return (
        db.query(ReconciliationJob)
        .filter(ReconciliationJob.session_id == session_id)
        .order_by(ReconciliationJob.created_at.desc())
        .limit(limit)
        .all()
    )


def search_jobs(
    db: Session,
    session_id: str,
    *,
    query_text: str = "",
    status_filter: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: str = "newest",
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[ReconciliationJob], int]:
    from app.models.file import UploadedFile

    first_file, second_file = aliased(UploadedFile), aliased(UploadedFile)
    query = (
        db.query(ReconciliationJob)
        .outerjoin(first_file, ReconciliationJob.input_file_1_id == first_file.id)
        .outerjoin(second_file, ReconciliationJob.input_file_2_id == second_file.id)
        .filter(ReconciliationJob.session_id == session_id)
    )
    if query_text.strip():
        term = f"%{query_text.strip()}%"
        query = query.filter(or_(
            ReconciliationJob.display_name.ilike(term),
            first_file.original_filename.ilike(term),
            second_file.original_filename.ilike(term),
        ))
    if status_filter:
        if status_filter not in {"queued", "processing", "completed", "completed_with_errors", "failed", "cancelled"}:
            raise ValueError("Unknown reconciliation status.")
        query = query.filter(ReconciliationJob.status == status_filter)
    if date_from:
        query = query.filter(ReconciliationJob.created_at >= datetime.combine(date_from, datetime.min.time(), tzinfo=timezone.utc))
    if date_to:
        query = query.filter(ReconciliationJob.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
    total = query.count()
    if sort == "oldest":
        query = query.order_by(ReconciliationJob.created_at.asc())
    elif sort == "name":
        query = query.order_by(ReconciliationJob.display_name.asc(), ReconciliationJob.created_at.desc())
    else:
        query = query.order_by(ReconciliationJob.created_at.desc())
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    return rows, total


def _job_file_ids(job: ReconciliationJob) -> set[str]:
    ids = {value for value in (job.input_file_1_id, job.input_file_2_id) if value}
    try:
        payload = json.loads(job.settings_json or "{}")
    except json.JSONDecodeError:
        return ids
    for pair in payload.get("file_pairs", []):
        for side in ("source_files", "destination_files"):
            ids.update(item.get("file_id") for item in pair.get(side, []) if item.get("file_id"))
    for side in ("source_files_1", "source_files_2"):
        ids.update(item.get("file_id") for item in payload.get(side, []) if item.get("file_id"))
    return ids


class PlanUnavailable(Exception):
    """The run's setup cannot be reopened; the message says why."""


def job_plan(db: Session, job_id: str, session_id: str) -> tuple[ReconciliationPlan, list[UploadedFile]] | None:
    """The stored plan of a general run and its uploaded files, in the order the plan uses them."""
    job = get_job(db, job_id, session_id)
    if job is None:
        return None
    if job.job_type != "generic":
        raise PlanUnavailable("Only general reconciliations can be changed and run again.")
    try:
        plan = ReconciliationPlan.model_validate(json.loads(job.settings_json or "{}"))
    except (ValueError, ValidationError) as exc:
        raise PlanUnavailable("This run's setup could not be read. Start a new reconciliation instead.") from exc
    if not plan.file_pairs:
        raise PlanUnavailable("This run has no saved setup. Start a new reconciliation instead.")

    file_ids = list(dict.fromkeys(
        source.file_id for pair in plan.file_pairs for source in [*pair.source_files, *pair.destination_files]
    ))
    records = {
        record.id: record
        for record in db.query(UploadedFile).filter(UploadedFile.id.in_(file_ids), UploadedFile.session_id == session_id)
    }
    if len(records) < len(file_ids):
        raise PlanUnavailable("The files from this run are no longer stored. Upload them again to reconcile.")
    return plan, [records[file_id] for file_id in file_ids]


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
        from app.services.quota_service import release_job_slot

        release_job_slot(db, job.id)
        db.commit()
        db.refresh(job)
    return job


def delete_job(db: Session, job_id: str, session_id: str, storage=None) -> bool:
    job = get_job(db, job_id, session_id)
    if not job:
        return False

    report_id = job.report_id
    file_ids = _job_file_ids(job)
    if job.status in {"queued", "processing"}:
        from app.services.quota_service import release_job_slot

        release_job_slot(db, job.id)

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
                from app.storage.base import report_data_path

                for path in (report.storage_path, report_data_path(report.storage_path)):
                    try:
                        storage.delete_file(path)
                    except Exception:
                        pass
            from app.services.quota_service import release_storage

            release_storage(db, session_id, report.size_bytes + report.data_size_bytes)
            db.delete(report)

    # Clean up uploaded files if no other job references them
    remaining_jobs = db.query(ReconciliationJob).filter(ReconciliationJob.session_id == session_id).all()
    referenced = set().union(*(_job_file_ids(other) for other in remaining_jobs)) if remaining_jobs else set()
    for file_id in file_ids - referenced:
        file_rec = db.query(UploadedFile).filter(UploadedFile.id == file_id, UploadedFile.session_id == session_id).first()
        if file_rec:
            if storage:
                try:
                    storage.delete_file(file_rec.storage_path)
                except Exception:
                    pass
            from app.services.quota_service import release_storage

            release_storage(db, session_id, file_rec.size_bytes)
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


def prune_old_jobs(db: Session, session_id: str, storage=None, max_records: int = 20, older_than_days: int | None = None) -> int:
    """
    Ensure a maximum of max_records (default 20) reconciliation records exist per session.
    Removes oldest excess records and cleans up storage files.
    """
    jobs_query = db.query(ReconciliationJob).filter(ReconciliationJob.session_id == session_id)
    if older_than_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        jobs = jobs_query.filter(ReconciliationJob.created_at < cutoff, ReconciliationJob.status.notin_(["queued", "processing"])).all()
        excess_jobs = jobs
        if not excess_jobs:
            return 0
    else:
        jobs = jobs_query.order_by(ReconciliationJob.created_at.desc()).all()
        if len(jobs) <= max_records:
            return 0
        excess_jobs = jobs[max_records:]
    pruned_count = 0
    pruned_ids = []
    for job in excess_jobs:
        job_id = job.id
        if delete_job(db, job_id, session_id, storage):
            pruned_count += 1
            pruned_ids.append(job_id)
    if pruned_ids:
        from app.services.audit_service import AuditActor, record_event

        record_event(
            scope_id=session_id, actor=AuditActor.system("retention"), action="job.pruned", entity_type="session",
            entity_id=session_id, summary=f"Removed {pruned_count} old reconciliation(s) beyond the {max_records}-record limit",
            before={"job_ids": pruned_ids},
        )
    return pruned_count
