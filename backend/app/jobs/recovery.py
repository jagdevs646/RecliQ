"""Recover jobs orphaned by a worker or API restart."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from app.core.config import get_settings
from app.database.session import SessionLocal
from app.models.job import ReconciliationJob
from app.services.audit_service import AuditActor, record_event
from app.services.job_service import append_history

logger = logging.getLogger("recliq.jobs")
_sweeper: threading.Thread | None = None
_sweeper_stop = threading.Event()


def _last_sign_of_life():
    return func.coalesce(ReconciliationJob.heartbeat_at, ReconciliationJob.started_at, ReconciliationJob.created_at)


def recover_orphaned_jobs(*, now: datetime | None = None, requeue_all_queued: bool = False) -> dict[str, int]:
    """Retry or fail jobs whose worker stopped, and re-queue lost queued jobs.

    A processing job is orphaned when its heartbeat is older than the lease.
    It is re-queued until it reaches the attempt limit, then failed with an
    explanation. Every transition is a conditional update, so concurrent
    sweepers never both act on the same job.
    """
    from app.jobs.queue import enqueue_job, is_enqueued

    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=settings.job_lease_seconds)
    counts = {"retried": 0, "failed": 0, "requeued": 0}
    to_enqueue: list[str] = []
    failed_jobs: list[tuple[str, str, int]] = []  # (job id, session id, attempts)
    db = SessionLocal()
    try:
        stale = db.scalars(
            select(ReconciliationJob).where(ReconciliationJob.status == "processing", _last_sign_of_life() < cutoff)
        ).all()
        for job in stale:
            exhausted = job.attempts >= settings.job_max_attempts
            values = (
                {
                    "status": "failed",
                    "progress": 100,
                    "completed_at": now,
                    "error_message": (
                        f"The worker running this job stopped responding {job.attempts} times. "
                        "The job was not retried again; run it again or contact support if it keeps failing."
                    ),
                }
                if exhausted
                else {"status": "queued", "progress": 0, "worker_id": None, "heartbeat_at": None}
            )
            result = db.execute(
                update(ReconciliationJob)
                .where(ReconciliationJob.id == job.id, ReconciliationJob.status == "processing", _last_sign_of_life() < cutoff)
                .values(**values)
            )
            if result.rowcount != 1:
                continue  # Another sweeper, or the worker itself, got there first.
            db.refresh(job)
            if exhausted:
                append_history(db, job, "failed", job.error_message or "Worker stopped responding")
                failed_jobs.append((job.id, job.session_id, job.attempts))
                counts["failed"] += 1
            else:
                append_history(
                    db, job, "queued",
                    f"The worker stopped responding; retrying (attempt {job.attempts + 1} of {settings.job_max_attempts}).",
                )
                to_enqueue.append(job.id)
                counts["retried"] += 1

        queued_filter = [ReconciliationJob.status == "queued"]
        if not requeue_all_queued:
            queued_filter.append(ReconciliationJob.updated_at < cutoff)
        for job in db.scalars(select(ReconciliationJob).where(*queued_filter)).all():
            if job.id in to_enqueue or is_enqueued(job.id):
                continue
            append_history(db, job, "queued", "Job re-queued after a restart.")
            to_enqueue.append(job.id)
            counts["requeued"] += 1
        db.commit()
    finally:
        db.close()

    for job_id, session_id, attempts in failed_jobs:
        record_event(
            scope_id=session_id, actor=AuditActor.system("job-recovery"), action="job.failed",
            entity_type="job", entity_id=job_id, summary="Job failed: worker stopped responding",
            after={"status": "failed", "attempts": attempts},
        )
    for job_id in to_enqueue:
        try:
            enqueue_job(job_id)
        except Exception:
            logger.exception("Could not re-queue job", extra={"recovered_job_id": job_id})
    if any(counts.values()):
        logger.info("Recovered orphaned jobs", extra=counts)
    return counts


def start_sweeper(interval_seconds: float | None = None) -> None:
    """Run recovery periodically in a daemon thread (idempotent)."""
    global _sweeper
    if _sweeper is not None and _sweeper.is_alive():
        return
    interval = interval_seconds or max(30.0, get_settings().job_lease_seconds / 3)
    _sweeper_stop.clear()

    def loop() -> None:
        while not _sweeper_stop.wait(interval):
            try:
                recover_orphaned_jobs()
            except Exception:
                logger.exception("Job recovery sweep failed")

    _sweeper = threading.Thread(target=loop, name="recliq-job-sweeper", daemon=True)
    _sweeper.start()


def stop_sweeper() -> None:
    _sweeper_stop.set()
