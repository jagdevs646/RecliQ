"""Guest workspaces are temporary.

A guest's uploads, runs, reports, saved setups and learning (aliases, review
decisions, auto-resolution rules and recurring patterns) are deleted once they
are older than ``GUEST_RETENTION_HOURS`` (24 by default). Registered accounts
(scope ``user-<id>``) are never touched. A guest who registers and imports the
workspace in time keeps it, because the import moves it to the account.

Kept on purpose: the append-only audit log (it cannot be changed) and support
tickets (the administrator needs them).

The cleanup runs in the web API only (``app.main``), never in the job sweeper:
the desktop app starts the sweeper and keeps all its data under one UUID scope.
"""
from __future__ import annotations

import logging
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import SessionLocal
from app.models.account import ReportAccessToken, ReportEmailDelivery, WorkspaceQuota
from app.models.alias import EntityAlias, MatchDecision
from app.models.file import UploadedFile
from app.models.job import ReconciliationJob
from app.models.report import Report
from app.models.resolution import ExceptionPattern, ResolutionRule
from app.models.template import ReconciliationTemplate, ReconciliationTemplateVersion
from app.services.audit_service import AuditActor, record_event
from app.services.job_service import _job_file_ids, delete_job
from app.services.quota_service import release_storage
from app.storage import get_storage
from app.storage.base import report_data_storage_paths

logger = logging.getLogger("recliq.retention")
ACTIVE_STATUSES = ("queued", "processing")

_cleanup_thread: threading.Thread | None = None
_cleanup_stop = threading.Event()


def _guest(column):
    """Rows of guest workspaces: every scope that is not an account's."""
    return ~column.like("user-%")


def _delete_object(storage, path: str) -> None:
    try:
        storage.delete_file(path)
    except Exception:
        logger.warning("Could not delete a stored guest object", exc_info=True)


def _delete_report_emails(db: Session, delivery_filter) -> None:
    delivery_ids = [row.id for row in db.query(ReportEmailDelivery.id).filter(delivery_filter)]
    if delivery_ids:
        db.query(ReportAccessToken).filter(ReportAccessToken.delivery_id.in_(delivery_ids)).delete(synchronize_session=False)
        db.query(ReportEmailDelivery).filter(ReportEmailDelivery.id.in_(delivery_ids)).delete(synchronize_session=False)


def purge_expired_guest_data(db: Session | None = None, storage=None, *, now: datetime | None = None) -> dict[str, int]:
    """Delete guest data older than GUEST_RETENTION_HOURS and return what was removed."""
    own_session = db is None
    db = db or SessionLocal()
    storage = storage or get_storage()
    now = now or datetime.now(timezone.utc)
    hours = get_settings().guest_retention_hours
    cutoff = now - timedelta(hours=hours)
    per_scope: dict[str, Counter] = defaultdict(Counter)
    try:
        # 1. Finished runs, with their report, history, reviews, emails and the
        #    uploads no other run uses. Each run is its own transaction.
        expired_runs = db.query(ReconciliationJob.id, ReconciliationJob.session_id).filter(
            _guest(ReconciliationJob.session_id),
            ReconciliationJob.created_at < cutoff,
            ReconciliationJob.status.notin_(ACTIVE_STATUSES),
        ).all()
        for job_id, scope in expired_runs:
            try:
                if delete_job(db, job_id, scope, storage):
                    per_scope[scope]["runs"] += 1
            except Exception:
                db.rollback()
                logger.warning("Could not delete an expired guest run", extra={"job_id": job_id}, exc_info=True)

        # 2. Uploads that no remaining run uses (never run, or their runs are gone).
        files_in_use: dict[str, set[str]] = {}
        for record in db.query(UploadedFile).filter(_guest(UploadedFile.session_id), UploadedFile.created_at < cutoff).all():
            if record.session_id not in files_in_use:
                runs = db.query(ReconciliationJob).filter(ReconciliationJob.session_id == record.session_id).all()
                files_in_use[record.session_id] = set().union(*(_job_file_ids(run) for run in runs))
            if record.id in files_in_use[record.session_id]:
                continue
            scope = record.session_id
            _delete_object(storage, record.storage_path)
            release_storage(db, scope, record.size_bytes)
            db.delete(record)
            db.commit()
            per_scope[scope]["files"] += 1

        # 3. Reports that no run points at any more (delete_job removes the rest).
        reports_in_use = select(ReconciliationJob.report_id).where(ReconciliationJob.report_id.is_not(None))
        for report in db.query(Report).filter(
            _guest(Report.session_id), Report.created_at < cutoff, Report.id.notin_(reports_in_use)
        ).all():
            scope = report.session_id
            _delete_report_emails(db, ReportEmailDelivery.report_id == report.id)
            for path in (report.storage_path, *report_data_storage_paths(report.storage_path)):
                _delete_object(storage, path)
            release_storage(db, scope, report.size_bytes + report.data_size_bytes)
            db.delete(report)
            db.commit()
            per_scope[scope]["reports"] += 1

        # 4. Saved setups and learning.
        for template_id, scope in db.query(ReconciliationTemplate.id, ReconciliationTemplate.session_id).filter(
            _guest(ReconciliationTemplate.session_id), ReconciliationTemplate.created_at < cutoff
        ).all():
            db.query(ReconciliationTemplateVersion).filter(ReconciliationTemplateVersion.template_id == template_id).delete(synchronize_session=False)
            db.query(ReconciliationTemplate).filter(ReconciliationTemplate.id == template_id).delete(synchronize_session=False)
            per_scope[scope]["saved_setups"] += 1
        for model, name in (
            (EntityAlias, "aliases"),
            (MatchDecision, "decisions"),
            (ResolutionRule, "rules"),
            (ExceptionPattern, "patterns"),
        ):
            for scope, in db.query(model.scope_id).filter(_guest(model.scope_id), model.created_at < cutoff).all():
                per_scope[scope][name] += 1
            db.query(model).filter(_guest(model.scope_id), model.created_at < cutoff).delete(synchronize_session=False)
        db.commit()

        # 5. Usage counters of guest workspaces that are now empty.
        workspaces_removed = 0
        for quota in db.query(WorkspaceQuota).filter(_guest(WorkspaceQuota.scope_id), WorkspaceQuota.active_jobs == 0).all():
            scope = quota.scope_id
            if any(
                db.query(model).filter(column == scope).first() is not None
                for model, column in (
                    (ReconciliationJob, ReconciliationJob.session_id),
                    (UploadedFile, UploadedFile.session_id),
                    (Report, Report.session_id),
                )
            ):
                continue
            db.delete(quota)
            workspaces_removed += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        if own_session:
            db.close()

    for scope, removed in per_scope.items():
        record_event(
            scope_id=scope, actor=AuditActor.system("retention"), action="workspace.guest_data_expired",
            entity_type="session", entity_id=scope,
            summary=f"Removed guest data older than {hours} hours",
            before=dict(removed),
        )
    totals = sum(per_scope.values(), Counter())
    result = {name: totals[name] for name in ("runs", "files", "reports", "saved_setups", "aliases", "decisions", "rules", "patterns")}
    result["workspaces"] = workspaces_removed
    if any(result.values()):
        logger.info("Expired guest data removed", extra=result)
    return result


def start_guest_cleanup(first_delay_seconds: float = 60.0, interval_seconds: float = 3600.0) -> None:
    """Delete expired guest data shortly after start-up, then every hour (idempotent)."""
    global _cleanup_thread
    if _cleanup_thread is not None and _cleanup_thread.is_alive():
        return
    _cleanup_stop.clear()

    def loop() -> None:
        delay = first_delay_seconds
        while not _cleanup_stop.wait(delay):
            delay = interval_seconds
            try:
                purge_expired_guest_data()
            except Exception:
                logger.exception("Guest data cleanup failed")

    _cleanup_thread = threading.Thread(target=loop, name="recliq-guest-cleanup", daemon=True)
    _cleanup_thread.start()


def stop_guest_cleanup() -> None:
    _cleanup_stop.set()
