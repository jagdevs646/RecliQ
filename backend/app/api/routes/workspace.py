from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_optional_user, get_session_id
from app.database.session import get_db
from app.models.account import User
from app.models.job import ReconciliationJob
from app.models.template import ReconciliationTemplate
from app.schemas.job import ReconciliationJobOut
from app.services.policy_service import effective_features, effective_limits
from app.services.quota_service import usage_snapshot


router = APIRouter(prefix="/workspace", tags=["workspace"])


def _start(period: str) -> datetime | None:
    now = datetime.now(timezone.utc)
    return now - timedelta(days=7 if period == "7d" else 30) if period in {"7d", "30d"} else None


def _job_out(job: ReconciliationJob) -> dict:
    return ReconciliationJobOut.model_validate(job).model_dump(mode="json")


@router.get("/overview")
def workspace_overview(
    period: Literal["7d", "30d", "all"] = "7d",
    db: Session = Depends(get_db),
    scope_id: str = Depends(get_session_id),
    user: User | None = Depends(get_optional_user),
) -> dict:
    start = _start(period)
    query = db.query(ReconciliationJob).filter(ReconciliationJob.session_id == scope_id)
    if start:
        query = query.filter(ReconciliationJob.created_at >= start)
    total = query.count()
    completed_query = query.filter(ReconciliationJob.status.in_(["completed", "completed_with_errors"]))
    completed = completed_query.count()
    failed = query.filter(ReconciliationJob.status == "failed").count()
    running = query.filter(ReconciliationJob.status.in_(["queued", "processing"])).count()
    matched = completed_query.with_entities(func.coalesce(func.sum(ReconciliationJob.matched_records_count), 0)).scalar() or 0
    exceptions = completed_query.with_entities(func.coalesce(func.sum(ReconciliationJob.exception_count), 0)).scalar() or 0
    review = completed_query.with_entities(func.coalesce(func.sum(ReconciliationJob.review_count), 0)).scalar() or 0

    chart_days = 30 if period == "30d" else 7
    chart_start = _start("30d" if chart_days == 30 else "7d")
    activity_rows = (
        db.query(func.date(ReconciliationJob.created_at), func.count(ReconciliationJob.id))
        .filter(ReconciliationJob.session_id == scope_id, ReconciliationJob.created_at >= chart_start)
        .group_by(func.date(ReconciliationJob.created_at))
        .all()
    )
    activity_counts = {str(day): count for day, count in activity_rows}
    today = datetime.now(timezone.utc).date()
    activity = [
        {"date": (today - timedelta(days=offset)).isoformat(), "runs": activity_counts.get((today - timedelta(days=offset)).isoformat(), 0)}
        for offset in range(chart_days - 1, -1, -1)
    ]

    attention_query = db.query(ReconciliationJob).filter(
        ReconciliationJob.session_id == scope_id,
        (ReconciliationJob.status.in_(["queued", "processing", "failed"]) | ((ReconciliationJob.status.in_(["completed", "completed_with_errors"])) & (ReconciliationJob.review_count > 0))),
    )
    attention = attention_query.order_by(ReconciliationJob.created_at.desc()).limit(8).all()
    recent = query.order_by(ReconciliationJob.created_at.desc()).limit(8).all()
    setups = (
        db.query(ReconciliationTemplate)
        .filter(ReconciliationTemplate.session_id == scope_id, ReconciliationTemplate.archived.is_(False))
        .order_by(ReconciliationTemplate.last_run_at.desc().nullslast(), ReconciliationTemplate.updated_at.desc())
        .limit(3)
        .all()
    )
    usage = usage_snapshot(db, scope_id)
    limits = effective_limits(db, user)

    return {
        "workspace": {
            "type": "account" if user else "guest",
            "label": user.display_name if user else "Guest Workspace",
            "user": {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role} if user else None,
        },
        "period": period,
        "metrics": {
            "runs": total,
            "matched_records": int(matched),
            "exceptions_flagged": int(exceptions),
            "awaiting_review": int(review),
            "completed_runs": completed,
            "failed_runs": failed,
            "active_runs": running,
        },
        "definitions": {
            "runs": "All reconciliations created in the selected period, including queued, failed, and completed runs.",
            "matched_records": "Sum of matched record counts from completed runs; each run contributes its own report count.",
            "exceptions_flagged": "Only-in-File-1 records plus only-in-File-2 records plus compared-field discrepancy counts. Auto-resolved items remain included as flagged history.",
            "awaiting_review": "Near-match proposals in completed reports awaiting a user decision; this is not the count of every detected difference.",
        },
        "activity": activity,
        "attention": [_job_out(job) for job in attention],
        "recent_jobs": [_job_out(job) for job in recent],
        "saved_setups": [{"id": item.id, "name": item.name, "description": item.description, "last_run_at": item.last_run_at, "current_version": item.current_version} for item in setups],
        "usage": {**usage, "storage_limit_bytes": limits["storage_mb"] * 1024 * 1024, "jobs_per_day_limit": limits["jobs_per_day"], "jobs_per_month_limit": limits["jobs_per_month"], "concurrent_jobs_limit": limits["concurrent_jobs"]},
        "features": effective_features(db, user),
    }
