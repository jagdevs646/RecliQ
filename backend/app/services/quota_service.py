from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.account import WorkspaceQuota
from app.models.file import UploadedFile
from app.models.job import ReconciliationJob
from app.models.report import Report


def _day_key(now: datetime) -> str:
    return now.date().isoformat()


def _month_key(now: datetime) -> str:
    return now.strftime("%Y-%m")


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _initial_quota(db: Session, scope_id: str, exclude_job_id: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    storage = (db.query(func.coalesce(func.sum(UploadedFile.size_bytes), 0)).filter(UploadedFile.session_id == scope_id).scalar() or 0)
    storage += db.query(func.coalesce(func.sum(Report.size_bytes + Report.data_size_bytes), 0)).filter(Report.session_id == scope_id).scalar() or 0
    jobs = db.query(ReconciliationJob).filter(ReconciliationJob.session_id == scope_id)
    day_query = jobs.filter(ReconciliationJob.created_at >= day_start)
    month_query = jobs.filter(ReconciliationJob.created_at >= month_start)
    if exclude_job_id:
        day_query = day_query.filter(ReconciliationJob.id != exclude_job_id)
        month_query = month_query.filter(ReconciliationJob.id != exclude_job_id)
    return {
        "scope_id": scope_id,
        "storage_bytes": int(storage),
        "day_key": _day_key(now),
        "jobs_today": day_query.count(),
        "month_key": _month_key(now),
        "jobs_month": month_query.count(),
        "active_jobs": jobs.filter(ReconciliationJob.quota_reserved.is_(True), ReconciliationJob.status.in_(["queued", "processing"])).count(),
        "email_day_key": _day_key(now),
        "emails_today": 0,
        "lock_version": 0,
    }


def _locked_quota(db: Session, scope_id: str, *, exclude_job_id: str | None = None) -> WorkspaceQuota:
    row = db.get(WorkspaceQuota, scope_id)
    if row is None:
        values = _initial_quota(db, scope_id, exclude_job_id)
        dialect = db.get_bind().dialect.name
        insert_statement = pg_insert(WorkspaceQuota).values(**values) if dialect == "postgresql" else sqlite_insert(WorkspaceQuota).values(**values)
        db.execute(insert_statement.on_conflict_do_nothing(index_elements=["scope_id"]))
    # The write obtains a per-workspace lock on both SQLite and PostgreSQL.
    db.execute(update(WorkspaceQuota).where(WorkspaceQuota.scope_id == scope_id).values(lock_version=WorkspaceQuota.lock_version + 1))
    row = db.scalar(select(WorkspaceQuota).where(WorkspaceQuota.scope_id == scope_id).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise RuntimeError("Could not reserve workspace quota")
    return row


def reserve_storage(db: Session, scope_id: str, size_bytes: int, limit_mb: int) -> None:
    quota = _locked_quota(db, scope_id)
    limit = limit_mb * 1024 * 1024
    if quota.storage_bytes + size_bytes > limit:
        raise HTTPException(status_code=413, detail={
            "code": "storage_limit_exceeded",
            "message": "This upload would exceed the workspace storage limit.",
            "current_bytes": quota.storage_bytes,
            "requested_bytes": size_bytes,
            "limit_bytes": limit,
        })
    quota.storage_bytes += size_bytes
    db.flush()


def release_storage(db: Session, scope_id: str, size_bytes: int) -> None:
    if size_bytes <= 0:
        return
    quota = _locked_quota(db, scope_id)
    quota.storage_bytes = max(0, quota.storage_bytes - size_bytes)
    db.flush()


def merge_workspace_usage(db: Session, source_scope: str, target_scope: str) -> None:
    """Transfer the source workspace's current-period quota counters once its data is imported."""
    now = datetime.now(timezone.utc)
    day = _day_key(now)
    month = _month_key(now)
    target = _locked_quota(db, target_scope)
    source = _locked_quota(db, source_scope)

    if target.day_key != day:
        target.day_key, target.jobs_today = day, 0
    if source.day_key == day:
        target.jobs_today += source.jobs_today
    if target.month_key != month:
        target.month_key, target.jobs_month = month, 0
    if source.month_key == month:
        target.jobs_month += source.jobs_month
    target.active_jobs += source.active_jobs
    if target.email_day_key != day:
        target.email_day_key, target.emails_today = day, 0
    if source.email_day_key == day:
        target.emails_today += source.emails_today
    source.day_key, source.jobs_today = day, 0
    source.month_key, source.jobs_month = month, 0
    source.active_jobs = 0
    source.email_day_key, source.emails_today = day, 0
    source.storage_bytes = 0
    db.flush()


def reserve_email(db: Session, scope_id: str, recipient_count: int, daily_limit: int) -> None:
    """Atomically reserve recipient sends for the UTC day, including retries."""
    now = datetime.now(timezone.utc)
    quota = _locked_quota(db, scope_id)
    day = _day_key(now)
    if quota.email_day_key != day:
        quota.email_day_key, quota.emails_today = day, 0
    if recipient_count <= 0 or quota.emails_today + recipient_count > daily_limit:
        raise HTTPException(status_code=429, detail={
            "code": "email_quota_exceeded",
            "message": "This request would exceed the workspace report-email limit for today.",
            "current_usage": quota.emails_today,
            "requested": recipient_count,
            "allowed": daily_limit,
        })
    quota.emails_today += recipient_count
    db.flush()


def reserve_job(db: Session, job: ReconciliationJob, limits: dict[str, int]) -> None:
    now = datetime.now(timezone.utc)
    quota = _locked_quota(db, job.session_id, exclude_job_id=job.id)
    day = _day_key(now)
    month = _month_key(now)
    if quota.day_key != day:
        quota.day_key, quota.jobs_today = day, 0
    if quota.month_key != month:
        quota.month_key, quota.jobs_month = month, 0
    checks = (
        ("jobs_per_day", quota.jobs_today, "jobs today"),
        ("jobs_per_month", quota.jobs_month, "jobs this month"),
        ("concurrent_jobs", quota.active_jobs, "active jobs"),
    )
    for limit_name, current, label in checks:
        limit = limits[limit_name]
        if current >= limit:
            raise HTTPException(status_code=429, detail={
                "code": "quota_exceeded",
                "limit": limit_name,
                "current_usage": current,
                "allowed": limit,
                "message": f"You have reached the limit of {limit} {label}.",
            })
    quota.jobs_today += 1
    quota.jobs_month += 1
    quota.active_jobs += 1
    job.quota_reserved = True
    db.flush()


def release_job_slot(db: Session, job_id: str) -> None:
    job = db.get(ReconciliationJob, job_id)
    if not job or not job.quota_reserved:
        return
    scope_id = job.session_id
    changed = db.execute(
        update(ReconciliationJob)
        .where(ReconciliationJob.id == job_id, ReconciliationJob.quota_reserved.is_(True))
        .values(quota_reserved=False)
    ).rowcount
    if not changed:
        return
    quota = _locked_quota(db, scope_id)
    quota.active_jobs = max(0, quota.active_jobs - 1)
    db.flush()


def usage_snapshot(db: Session, scope_id: str) -> dict[str, int]:
    quota = db.get(WorkspaceQuota, scope_id)
    if quota:
        return {
            "storage_bytes": quota.storage_bytes,
            "jobs_today": quota.jobs_today if quota.day_key == _day_key(datetime.now(timezone.utc)) else 0,
            "jobs_month": quota.jobs_month if quota.month_key == _month_key(datetime.now(timezone.utc)) else 0,
            "active_jobs": quota.active_jobs,
        }
    values = _initial_quota(db, scope_id)
    return {"storage_bytes": values["storage_bytes"], "jobs_today": values["jobs_today"], "jobs_month": values["jobs_month"], "active_jobs": values["active_jobs"]}
