from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, literal, or_, select, text, union_all
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import get_settings
from app.core.upload_validation import SUPPORTED_FORMATS
from app.database.session import get_db
from app.models.account import AdminAuditEvent, PlatformEmailSettings, PlatformPolicy, ReportEmailDelivery, User, UserPolicyOverride, UserPreference, UserSession
from app.models.file import UploadedFile
from app.models.job import ReconciliationJob
from app.models.report import Report
from app.models.alias import EntityAlias, MatchDecision
from app.models.audit import AuditEvent
from app.models.resolution import ExceptionPattern, ResolutionRule
from app.models.support import SupportTicket
from app.models.template import ReconciliationTemplate
from app.services.account_security import record_admin_event
from app.services.email_service import EMAIL_SETTINGS_ID, email_transport_status, send_email
from app.services.policy_service import FEATURE_DEFAULTS, default_limits, effective_features, effective_limits, get_global_policy
from app.storage import get_storage


router = APIRouter(prefix="/admin", tags=["administration"], dependencies=[Depends(require_admin)])
USER_STATUSES = {"active", "restricted", "suspended", "deleted"}
class StatusUpdate(BaseModel):
    status: Literal["active", "restricted", "suspended"]
    reason: str = Field(default="", max_length=500)


class PolicyUpdate(BaseModel):
    features: dict[str, bool] | None = None
    limits: dict[str, dict[str, int | list[str]]] | None = None


class UserPolicyUpdate(BaseModel):
    features: dict[str, bool] = Field(default_factory=dict)
    limits: dict[str, int | list[str]] = Field(default_factory=dict)


class EmailSenderUpdate(BaseModel):
    sender_email: EmailStr


class ReasonPayload(BaseModel):
    reason: str = Field(min_length=5, max_length=500)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("Provide an audit reason with at least five characters.")
        return value


def _range_start(period: str) -> datetime | None:
    now = datetime.now(timezone.utc)
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "7d":
        return now - timedelta(days=7)
    if period == "30d":
        return now - timedelta(days=30)
    return None


def _parse_date(value: date | None, *, end_of_day: bool = False) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, datetime.max.time() if end_of_day else datetime.min.time(), tzinfo=timezone.utc)


def _json(value: str | None, fallback):
    try:
        return json.loads(value) if value else fallback
    except json.JSONDecodeError:
        return fallback


def _bounded_limits(limits: dict[str, int | list[str]]) -> dict[str, int | list[str]]:
    settings = get_settings()
    ranges = {
        "max_file_size_mb": (1, max(1, min(500, settings.max_upload_mb))),
        "max_files_per_run": (2, 100),
        "max_rows_per_sheet": (1, max(1, min(5_000_000, settings.max_rows_per_sheet))),
        "max_rows_per_run": (1000, 20_000_000),
        "jobs_per_day": (1, 100_000),
        "jobs_per_month": (1, 1_000_000),
        "concurrent_jobs": (1, 100),
        "storage_mb": (50, 1_000_000),
        "report_retention_days": (0, 3650),
        "report_emails_per_day": (0, 1000),
        "max_email_recipients": (0, 50),
    }
    unknown = set(limits) - set(ranges)
    unknown -= {"allowed_formats"}
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unsupported limit: {sorted(unknown)[0]}")
    for name, value in limits.items():
        if name == "allowed_formats":
            if not isinstance(value, list):
                raise HTTPException(status_code=422, detail="allowed_formats must be a list of supported file extensions.")
            normalized = sorted({item.lower() for item in value if isinstance(item, str)})
            if len(normalized) != len(value) or set(normalized) - set(SUPPORTED_FORMATS):
                raise HTTPException(status_code=422, detail="allowed_formats contains an unsupported extension.")
            limits[name] = normalized
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise HTTPException(status_code=422, detail=f"{name} must be an integer.")
        low, high = ranges[name]
        if not low <= value <= high:
            raise HTTPException(status_code=422, detail=f"{name} must be between {low} and {high}.")
    return limits


def _account_usage(db: Session, user_id: str) -> dict:
    scope_id = f"user-{user_id}"
    jobs = db.query(ReconciliationJob.status, func.count(ReconciliationJob.id)).filter(ReconciliationJob.session_id == scope_id).group_by(ReconciliationJob.status).all()
    files = db.query(func.count(UploadedFile.id), func.coalesce(func.sum(UploadedFile.size_bytes), 0)).filter(UploadedFile.session_id == scope_id).one()
    reports = db.query(func.count(Report.id), func.coalesce(func.sum(Report.size_bytes + Report.data_size_bytes), 0)).filter(Report.session_id == scope_id).one()
    return {
        "jobs_by_status": {status_name: count for status_name, count in jobs},
        "reconciliation_count": sum(count for _, count in jobs),
        "file_count": files[0],
        "uploaded_bytes": int(files[1]),
        "report_count": reports[0],
        "report_bytes": int(reports[1]),
        "storage_bytes": int(files[1]) + int(reports[1]),
    }


def _account_usage_many(db: Session, users: list[User]) -> dict[str, dict]:
    scopes = {f"user-{user.id}": user.id for user in users}
    usage_by_user = {
        user.id: {
            "jobs_by_status": {}, "reconciliation_count": 0, "file_count": 0,
            "uploaded_bytes": 0, "report_count": 0, "report_bytes": 0, "storage_bytes": 0,
        }
        for user in users
    }
    if not scopes:
        return usage_by_user
    for scope, status_name, count in db.query(
        ReconciliationJob.session_id, ReconciliationJob.status, func.count(ReconciliationJob.id)
    ).filter(ReconciliationJob.session_id.in_(scopes)).group_by(
        ReconciliationJob.session_id, ReconciliationJob.status
    ):
        usage = usage_by_user[scopes[scope]]
        usage["jobs_by_status"][status_name] = count
        usage["reconciliation_count"] += count
    for scope, count, size in db.query(
        UploadedFile.session_id, func.count(UploadedFile.id), func.coalesce(func.sum(UploadedFile.size_bytes), 0)
    ).filter(UploadedFile.session_id.in_(scopes)).group_by(UploadedFile.session_id):
        usage = usage_by_user[scopes[scope]]
        usage["file_count"] = count
        usage["uploaded_bytes"] = int(size)
    for scope, count, size in db.query(
        Report.session_id, func.count(Report.id),
        func.coalesce(func.sum(Report.size_bytes + Report.data_size_bytes), 0),
    ).filter(Report.session_id.in_(scopes)).group_by(Report.session_id):
        usage = usage_by_user[scopes[scope]]
        usage["report_count"] = count
        usage["report_bytes"] = int(size)
    for usage in usage_by_user.values():
        usage["storage_bytes"] = usage["uploaded_bytes"] + usage["report_bytes"]
    return usage_by_user


@router.get("/overview")
def overview(
    period: Literal["today", "7d", "30d", "all", "custom"] = "7d",
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    start = _range_start(period) if period != "custom" else _parse_date(date_from)
    end = _parse_date(date_to, end_of_day=True) if period == "custom" else None
    if period == "custom" and (start is None or end is None or end < start):
        raise HTTPException(status_code=422, detail="Choose a valid custom date range.")
    user_query = db.query(User)
    job_query = db.query(ReconciliationJob)
    file_query = db.query(UploadedFile)
    report_query = db.query(Report)
    email_query = db.query(ReportEmailDelivery)
    if start:
        user_query = user_query.filter(User.created_at >= start)
        job_query = job_query.filter(ReconciliationJob.created_at >= start)
        file_query = file_query.filter(UploadedFile.created_at >= start)
        report_query = report_query.filter(Report.created_at >= start)
        email_query = email_query.filter(ReportEmailDelivery.created_at >= start)
    if end:
        user_query = user_query.filter(User.created_at <= end)
        job_query = job_query.filter(ReconciliationJob.created_at <= end)
        file_query = file_query.filter(UploadedFile.created_at <= end)
        report_query = report_query.filter(Report.created_at <= end)
        email_query = email_query.filter(ReportEmailDelivery.created_at <= end)

    accounts = {
        "total": db.query(func.count(User.id)).scalar() or 0,
        "active": db.query(func.count(User.id)).filter(User.status == "active").scalar() or 0,
        "suspended": db.query(func.count(User.id)).filter(User.status == "suspended").scalar() or 0,
        "restricted": db.query(func.count(User.id)).filter(User.status == "restricted").scalar() or 0,
        "new_in_period": user_query.count(),
        "active_in_period": db.query(func.count(User.id)).filter(User.status == "active", User.last_seen_at >= start).scalar() if start else db.query(func.count(User.id)).filter(User.status == "active", User.last_seen_at.is_not(None)).scalar(),
    }
    jobs = {
        "total": job_query.count(),
        "completed": job_query.filter(ReconciliationJob.status.in_(["completed", "completed_with_errors"])).count(),
        "failed": job_query.filter(ReconciliationJob.status == "failed").count(),
        "queued_or_processing": job_query.filter(ReconciliationJob.status.in_(["queued", "processing"])).count(),
        "registered": job_query.filter(ReconciliationJob.session_id.like("user-%")).count(),
        "guest": job_query.filter(~ReconciliationJob.session_id.like("user-%")).count(),
    }
    email_counts = {
        name: email_query.filter(ReportEmailDelivery.status == name).count()
        for name in ("queued", "processing", "failed")
    }
    email_counts["sent"] = email_query.filter(
        ReportEmailDelivery.status == "sent", ReportEmailDelivery.transport == "smtp"
    ).count()
    email_counts["logged_locally"] = email_query.filter(
        ReportEmailDelivery.status == "sent", ReportEmailDelivery.transport == "local_log"
    ).count()
    complaint_query = db.query(SupportTicket)
    if start:
        complaint_query = complaint_query.filter(SupportTicket.created_at >= start)
    if end:
        complaint_query = complaint_query.filter(SupportTicket.created_at <= end)
    complaint_by_category = {
        category: count
        for category, count in complaint_query.with_entities(SupportTicket.category, func.count(SupportTicket.id)).group_by(SupportTicket.category).all()
    }
    complaint_priority = dict(complaint_query.with_entities(SupportTicket.priority, func.count(SupportTicket.id)).group_by(SupportTicket.priority).all())
    open_complaints = db.query(SupportTicket).filter(SupportTicket.status != "resolved")
    daily_jobs = []
    daily_accounts = []
    if start:
        daily_jobs = [
            {"date": str(day), "count": count}
            for day, count in db.query(func.date(ReconciliationJob.created_at), func.count(ReconciliationJob.id))
            .filter(ReconciliationJob.created_at >= start, *([ReconciliationJob.created_at <= end] if end else []))
            .group_by(func.date(ReconciliationJob.created_at)).order_by(func.date(ReconciliationJob.created_at)).all()
        ]
        daily_accounts = [
            {"date": str(day), "count": count}
            for day, count in db.query(func.date(User.created_at), func.count(User.id))
            .filter(User.created_at >= start, *([User.created_at <= end] if end else []))
            .group_by(func.date(User.created_at)).order_by(func.date(User.created_at)).all()
        ]
    return {
        "period": period,
        "accounts": accounts,
        "jobs": jobs,
        "files": {"count": file_query.count(), "bytes": int(file_query.with_entities(func.coalesce(func.sum(UploadedFile.size_bytes), 0)).scalar() or 0)},
        "reports": {"count": report_query.count(), "bytes": int(report_query.with_entities(func.coalesce(func.sum(Report.size_bytes + Report.data_size_bytes), 0)).scalar() or 0)},
        "emails": email_counts,
        "complaints": {
            "total": complaint_query.count(),
            "unresolved": complaint_query.filter(SupportTicket.status != "resolved").count(),
            "unread": db.query(func.count(SupportTicket.id)).filter(SupportTicket.admin_read_at.is_(None)).scalar() or 0,
            "new": complaint_query.filter(SupportTicket.status == "new").count(),
            "urgent": open_complaints.filter(SupportTicket.priority == "urgent").count(),
            "high": open_complaints.filter(SupportTicket.priority == "high").count(),
            "by_priority": complaint_priority,
            "by_category": complaint_by_category,
        },
        "activity": {"jobs": daily_jobs, "accounts": daily_accounts},
        "recent_admin_activity": [
            {"id": event.event_id, "occurred_at": event.occurred_at, "actor_id": event.actor_id, "action": event.action, "target_type": event.target_type, "target_id": event.target_id, "result": event.result}
            for event in db.scalars(select(AdminAuditEvent).order_by(AdminAuditEvent.id.desc()).limit(10))
        ],
        "period_start": start,
    }


@router.get("/users")
def users(
    q: str = "",
    account_status: str | None = Query(default=None, alias="status"),
    role: str | None = None,
    sort: Literal["created_desc", "created_asc", "username", "activity_desc", "runs_desc", "storage_desc", "status"] = "created_desc",
    created_from: date | None = None,
    created_to: date | None = None,
    active_from: date | None = None,
    active_to: date | None = None,
    runs_min: int | None = Query(default=None, ge=0),
    runs_max: int | None = Query(default=None, ge=0),
    storage_min: int | None = Query(default=None, ge=0),
    storage_max: int | None = Query(default=None, ge=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(User)
    if q.strip():
        term = f"%{q.strip().casefold()}%"
        query = query.filter(or_(User.id.like(term), User.username_key.like(term), User.email_key.like(term), func.lower(User.display_name).like(term)))
    if account_status:
        if account_status not in USER_STATUSES:
            raise HTTPException(status_code=422, detail="Unknown account status.")
        query = query.filter(User.status == account_status)
    if role:
        if role not in {"user", "super_admin"}:
            raise HTTPException(status_code=422, detail="Unknown account role.")
        query = query.filter(User.role == role)
    session_scope = literal("user-").concat(User.id)
    run_count = select(func.count(ReconciliationJob.id)).where(
        ReconciliationJob.session_id == session_scope
    ).correlate(User).scalar_subquery()
    upload_bytes = select(func.coalesce(func.sum(UploadedFile.size_bytes), 0)).where(
        UploadedFile.session_id == session_scope
    ).correlate(User).scalar_subquery()
    report_bytes = select(func.coalesce(func.sum(Report.size_bytes + Report.data_size_bytes), 0)).where(
        Report.session_id == session_scope
    ).correlate(User).scalar_subquery()
    storage_bytes = upload_bytes + report_bytes
    if created_from:
        query = query.filter(User.created_at >= _parse_date(created_from))
    if created_to:
        query = query.filter(User.created_at <= _parse_date(created_to, end_of_day=True))
    if active_from:
        query = query.filter(User.last_seen_at >= _parse_date(active_from))
    if active_to:
        query = query.filter(User.last_seen_at <= _parse_date(active_to, end_of_day=True))
    if created_from and created_to and created_to < created_from:
        raise HTTPException(status_code=422, detail="Registration end date must be on or after the start date.")
    if active_from and active_to and active_to < active_from:
        raise HTTPException(status_code=422, detail="Activity end date must be on or after the start date.")
    if runs_min is not None:
        query = query.filter(run_count >= runs_min)
    if runs_max is not None:
        query = query.filter(run_count <= runs_max)
    if storage_min is not None:
        query = query.filter(storage_bytes >= storage_min)
    if storage_max is not None:
        query = query.filter(storage_bytes <= storage_max)
    if runs_min is not None and runs_max is not None and runs_max < runs_min:
        raise HTTPException(status_code=422, detail="Maximum runs must be greater than or equal to minimum runs.")
    if storage_min is not None and storage_max is not None and storage_max < storage_min:
        raise HTTPException(status_code=422, detail="Maximum storage must be greater than or equal to minimum storage.")
    total = query.count()
    if sort == "created_asc":
        ordering = User.created_at.asc()
    elif sort == "username":
        ordering = User.username_key.asc()
    elif sort == "activity_desc":
        ordering = User.last_seen_at.desc().nullslast()
    elif sort == "status":
        ordering = User.status.asc()
    elif sort == "runs_desc":
        ordering = run_count.desc()
    elif sort == "storage_desc":
        ordering = storage_bytes.desc()
    else:
        ordering = User.created_at.desc()
    records = query.order_by(ordering).offset((page - 1) * page_size).limit(page_size).all()
    usage_by_user = _account_usage_many(db, records)
    return {
        "users": [{
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
            "status": user.status,
            "email_verified": user.email_verified,
            "created_at": user.created_at,
            "last_seen_at": user.last_seen_at,
            "usage": usage_by_user[user.id],
        } for user in records],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/users/{user_id}")
def user_detail(user_id: str, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Account not found.")
    sessions = db.scalars(select(UserSession).where(UserSession.user_id == user.id).order_by(UserSession.last_seen_at.desc()).limit(50)).all()
    preference = db.get(UserPreference, user.id)
    override = db.get(UserPolicyOverride, user.id)
    scope_id = f"user-{user.id}"
    audit_rows = db.scalars(select(AdminAuditEvent).where(AdminAuditEvent.target_id == user.id).order_by(AdminAuditEvent.id.desc()).limit(25)).all()
    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
            "status": user.status,
            "email_verified": user.email_verified,
            "mfa_enabled": user.mfa_enabled,
            "created_at": user.created_at,
            "last_login_at": user.last_login_at,
            "last_seen_at": user.last_seen_at,
        },
        "usage": _account_usage(db, user.id),
        "limits": effective_limits(db, user),
        "features": effective_features(db, user),
        "feature_overrides": _json(override.feature_overrides_json if override else None, {}),
        "limit_overrides": _json(override.limits_overrides_json if override else None, {}),
        "preferences": {"theme": preference.theme, "notifications": _json(preference.notifications_json, {})} if preference else {"theme": "system", "notifications": {}},
        "sessions": [{"id": item.id, "created_at": item.created_at, "last_seen_at": item.last_seen_at, "expires_at": item.expires_at, "revoked_at": item.revoked_at, "ip_address": item.ip_address, "user_agent": item.user_agent} for item in sessions],
        "report_emails": [
            {"id": item.id, "job_id": item.job_id, "status": item.status, "recipients": _json(item.recipients_json, []), "created_at": item.created_at, "sent_at": item.sent_at, "error_message": item.error_message}
            for item in db.scalars(select(ReportEmailDelivery).where(ReportEmailDelivery.sender_user_id == user.id).order_by(ReportEmailDelivery.created_at.desc()).limit(50))
        ],
        "admin_events": [{"id": item.event_id, "action": item.action, "occurred_at": item.occurred_at, "result": item.result, "before": _json(item.before_json, None), "after": _json(item.after_json, None)} for item in audit_rows],
        "workspace": scope_id,
        "complaints": db.query(func.count(SupportTicket.id)).filter(SupportTicket.requester_user_id == user.id).scalar() or 0,
    }


@router.get("/product-insights")
def product_insights(period: Literal["today", "7d", "30d", "all"] = "all", db: Session = Depends(get_db)) -> dict:
    """Aggregate feature adoption without returning customer-entered content."""
    start = _range_start(period)
    template_query = db.query(ReconciliationTemplate).filter(ReconciliationTemplate.archived.is_(False))
    alias_query = db.query(EntityAlias).filter(EntityAlias.active.is_(True))
    rule_query = db.query(ResolutionRule).filter(ResolutionRule.enabled.is_(True), ResolutionRule.archived.is_(False))
    decision_query = db.query(MatchDecision).filter(MatchDecision.decision.in_(["accept", "reject"]))
    pattern_query = db.query(ExceptionPattern)
    if start:
        template_query = template_query.filter(ReconciliationTemplate.created_at >= start)
        alias_query = alias_query.filter(EntityAlias.created_at >= start)
        rule_query = rule_query.filter(ResolutionRule.created_at >= start)
        decision_query = decision_query.filter(MatchDecision.created_at >= start)
        pattern_query = pattern_query.filter(ExceptionPattern.last_seen_at >= start)
    templates = template_query.with_entities(func.count(ReconciliationTemplate.id), func.count(func.distinct(ReconciliationTemplate.session_id))).one()
    aliases = alias_query.with_entities(func.count(EntityAlias.id), func.count(func.distinct(EntityAlias.scope_id))).one()
    rules = rule_query.with_entities(func.count(ResolutionRule.id), func.count(func.distinct(ResolutionRule.scope_id))).one()
    decisions = dict(decision_query.with_entities(MatchDecision.decision, func.count(MatchDecision.id)).group_by(MatchDecision.decision).all())
    patterns = pattern_query.with_entities(func.count(ExceptionPattern.id), func.coalesce(func.sum(ExceptionPattern.occurrences), 0), func.count(func.distinct(ExceptionPattern.scope_id))).one()
    return {
        "period": period,
        "saved_setups": {"total": templates[0], "workspaces": templates[1]},
        "aliases": {"active": aliases[0], "workspaces": aliases[1]},
        "auto_resolution": {"active_rules": rules[0], "workspaces": rules[1], "observed_exception_occurrences": int(patterns[1]), "workspaces_with_patterns": patterns[2]},
        "learning": {"confirmed_pairings": decisions.get("accept", 0), "rejected_pairings": decisions.get("reject", 0), "workspaces": db.query(func.count(func.distinct(MatchDecision.scope_id))).scalar() or 0},
    }


@router.get("/activity")
def platform_activity(
    q: str = "",
    action: str | None = None,
    source: Literal["all", "workspace", "admin"] = "all",
    date_from: date | None = None,
    date_to: date | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    parts = []
    if source in {"all", "workspace"}:
        parts.append(select(
            AuditEvent.id.label("sequence"), AuditEvent.event_id.label("event_id"), AuditEvent.occurred_at.label("occurred_at"),
            AuditEvent.actor_type.label("actor_type"), AuditEvent.actor_id.label("actor_id"), AuditEvent.scope_id.label("scope_id"),
            AuditEvent.action.label("action"), AuditEvent.entity_type.label("target_type"), AuditEvent.entity_id.label("target_id"),
            AuditEvent.summary.label("summary"), literal("workspace").label("source"),
        ))
    if source in {"all", "admin"}:
        parts.append(select(
            AdminAuditEvent.id.label("sequence"), AdminAuditEvent.event_id.label("event_id"), AdminAuditEvent.occurred_at.label("occurred_at"),
            literal("administrator").label("actor_type"), AdminAuditEvent.actor_id.label("actor_id"), literal("platform").label("scope_id"),
            AdminAuditEvent.action.label("action"), AdminAuditEvent.target_type.label("target_type"), AdminAuditEvent.target_id.label("target_id"),
            literal("").label("summary"), literal("admin").label("source"),
        ))
    query = union_all(*parts).subquery()
    statement = select(query)
    if action:
        statement = statement.where(query.c.action == action)
    if date_from:
        statement = statement.where(query.c.occurred_at >= _parse_date(date_from))
    if date_to:
        statement = statement.where(query.c.occurred_at <= _parse_date(date_to, end_of_day=True))
    if q.strip():
        term = f"%{q.strip().casefold()}%"
        matching_users = select(User.id).where(or_(
            func.lower(User.username).like(term),
            func.lower(User.email).like(term),
            func.lower(User.display_name).like(term),
            func.lower(User.id).like(term),
        ))
        matching_scopes = select(literal("user-").concat(User.id)).where(or_(
            func.lower(User.username).like(term),
            func.lower(User.email).like(term),
            func.lower(User.display_name).like(term),
            func.lower(User.id).like(term),
        ))
        statement = statement.where(or_(
            func.lower(query.c.action).like(term), func.lower(query.c.actor_id).like(term),
            func.lower(query.c.target_id).like(term), func.lower(query.c.scope_id).like(term),
            func.lower(query.c.summary).like(term), query.c.actor_id.in_(matching_users),
            query.c.scope_id.in_(matching_scopes),
        ))
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = db.execute(statement.order_by(query.c.occurred_at.desc(), query.c.sequence.desc()).offset(offset).limit(limit)).mappings().all()
    return {"events": [dict(row) for row in rows], "total": total, "offset": offset, "limit": limit}


@router.put("/users/{user_id}/status")
def update_user_status(user_id: str, payload: StatusUpdate, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Account not found.")
    if len(payload.reason.strip()) < 5:
        raise HTTPException(status_code=422, detail="Provide an audit reason with at least five characters.")
    if user.id == admin.id and payload.status != "active":
        raise HTTPException(status_code=422, detail="You cannot restrict or suspend your own administrator account.")
    before = {"status": user.status}
    if user.role == "super_admin" and payload.status != "active":
        active_admins = db.query(func.count(User.id)).filter(User.role == "super_admin", User.status == "active").scalar() or 0
        if active_admins <= 1:
            raise HTTPException(status_code=409, detail="The last active Super Admin cannot be suspended or restricted.")
    user.status = payload.status
    if payload.status == "suspended":
        user_sessions = db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        user_sessions.update({UserSession.revoked_at: datetime.now(timezone.utc)}, synchronize_session=False)
    record_admin_event(db, request, actor_id=admin.id, action="user.status_changed", target_type="user", target_id=user.id, before=before, after={"status": user.status}, metadata={"reason": payload.reason.strip()})
    db.commit()
    return {"id": user.id, "status": user.status}


@router.post("/users/{user_id}/revoke-sessions")
def revoke_user_sessions(user_id: str, payload: ReasonPayload, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, int]:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Account not found.")
    if user.role == "super_admin":
        raise HTTPException(status_code=409, detail="Super Admin sessions cannot be revoked from the user directory.")
    now = datetime.now(timezone.utc)
    result = db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).update({UserSession.revoked_at: now}, synchronize_session=False)
    record_admin_event(db, request, actor_id=admin.id, action="user.sessions_revoked", target_type="user", target_id=user.id, after={"revoked_count": result}, metadata={"reason": payload.reason.strip()})
    db.commit()
    return {"revoked_count": result}


@router.put("/users/{user_id}/policy")
def update_user_policy(user_id: str, payload: UserPolicyUpdate, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Account not found.")
    if user.role == "super_admin":
        raise HTTPException(status_code=409, detail="Super Admin policy cannot be restricted from the user directory.")
    unknown_features = set(payload.features) - set(FEATURE_DEFAULTS)
    if unknown_features:
        raise HTTPException(status_code=422, detail=f"Unsupported feature: {sorted(unknown_features)[0]}")
    limits = _bounded_limits(payload.limits)
    override = db.get(UserPolicyOverride, user.id)
    if not override:
        override = UserPolicyOverride(user_id=user.id)
        db.add(override)
    before = {"features": _json(override.feature_overrides_json, {}), "limits": _json(override.limits_overrides_json, {})}
    override.feature_overrides_json = json.dumps(payload.features, sort_keys=True)
    override.limits_overrides_json = json.dumps(limits, sort_keys=True)
    override.updated_by = admin.id
    record_admin_event(db, request, actor_id=admin.id, action="user.policy_changed", target_type="user", target_id=user.id, before=before, after={"features": payload.features, "limits": limits})
    db.commit()
    return {"features": effective_features(db, user), "limits": effective_limits(db, user)}


@router.post("/users/{user_id}/require-password-reset")
def require_password_reset(user_id: str, payload: ReasonPayload, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, str]:
    user = db.get(User, user_id)
    if not user or user.role == "super_admin":
        raise HTTPException(status_code=404, detail="Account not found.")
    user.require_password_reset = True
    now = datetime.now(timezone.utc)
    db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).update({UserSession.revoked_at: now}, synchronize_session=False)
    record_admin_event(db, request, actor_id=admin.id, action="user.password_reset_required", target_type="user", target_id=user.id, after={"required": True}, metadata={"reason": payload.reason.strip()})
    db.commit()
    return {"message": "The account must reset its password before signing in."}


@router.post("/users/{user_id}/delete")
def delete_user(user_id: str, payload: ReasonPayload, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, str]:
    user = db.get(User, user_id)
    if not user or user.role == "super_admin" or user.id == admin.id:
        raise HTTPException(status_code=404, detail="Account not found.")
    before_status = user.status
    user.status = "deleted"
    now = datetime.now(timezone.utc)
    db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).update({UserSession.revoked_at: now}, synchronize_session=False)
    record_admin_event(db, request, actor_id=admin.id, action="user.deletion_requested", target_type="user", target_id=user.id, before={"status": before_status}, after={"status": "deleted", "data_retained": True}, metadata={"reason": payload.reason.strip()})
    db.commit()
    return {"message": "Account access has been revoked. Data remains retained for controlled deletion and audit preservation."}


@router.get("/policy")
def get_policy(db: Session = Depends(get_db)) -> dict:
    policy = get_global_policy(db, create=True)
    db.commit()
    features = {**FEATURE_DEFAULTS, **_json(policy.feature_flags_json, {})}
    limits = {**default_limits(), **_json(policy.limits_json, {})}
    email_ready = bool(email_transport_status(db)["ready"])
    return {
        "features": features,
        "limits": limits,
        "available_formats": [{"extension": extension, **details} for extension, details in SUPPORTED_FORMATS.items()],
        "global_default_theme": policy.default_theme,
        "email_ready": email_ready,
    }


@router.put("/policy")
def update_policy(payload: PolicyUpdate, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    policy = get_global_policy(db, create=True)
    before = {"features": _json(policy.feature_flags_json, {}), "limits": _json(policy.limits_json, {}), "default_theme": policy.default_theme}
    features = before["features"]
    if payload.features is not None:
        unknown = set(payload.features) - set(FEATURE_DEFAULTS)
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unsupported feature: {sorted(unknown)[0]}")
        features = {**FEATURE_DEFAULTS, **features, **payload.features}
        features["registration"] = bool(features["registration"] and get_settings().registration_enabled)
    limits = before["limits"]
    if payload.limits is not None:
        if set(payload.limits) - {"guest", "registered"}:
            raise HTTPException(status_code=422, detail="Limits must be grouped under guest or registered.")
        limits = {**default_limits(), **limits}
        for group, values in payload.limits.items():
            limits[group] = {**limits.get(group, {}), **_bounded_limits(values)}
            if not limits[group].get("allowed_formats"):
                raise HTTPException(status_code=422, detail=f"At least one supported upload format must remain enabled for {group} workspaces.")
    policy.feature_flags_json = json.dumps(features, sort_keys=True)
    policy.limits_json = json.dumps(limits, sort_keys=True)
    policy.updated_by = admin.id
    record_admin_event(db, request, actor_id=admin.id, action="platform.policy_changed", target_type="platform_policy", target_id="global", before=before, after={"features": features, "limits": limits, "default_theme": policy.default_theme})
    db.commit()
    return get_policy(db)


@router.get("/email-settings")
def get_email_settings(db: Session = Depends(get_db)) -> dict:
    return email_transport_status(db)


@router.put("/email-settings")
def update_email_settings(payload: EmailSenderUpdate, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    sender_email = str(payload.sender_email).strip().lower()
    current_status = email_transport_status(db)
    previous = str(current_status["sender_email"])
    if sender_email == previous.casefold():
        return current_status
    row = db.get(PlatformEmailSettings, EMAIL_SETTINGS_ID)
    if row is None:
        row = PlatformEmailSettings(id=EMAIL_SETTINGS_ID, sender_email=sender_email, updated_by=admin.id)
        db.add(row)
    else:
        row.sender_email = sender_email
        row.updated_by = admin.id
    record_admin_event(
        db, request, actor_id=admin.id, action="platform.email_sender_changed",
        target_type="platform_email_settings", target_id=EMAIL_SETTINGS_ID,
        before={"sender_email": previous}, after={"sender_email": sender_email},
    )
    db.commit()
    return email_transport_status(db)


@router.post("/email-settings/test")
def send_email_settings_test(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, str]:
    if not admin.email_verified:
        raise HTTPException(status_code=409, detail="Verify the administrator email address before sending a test email.")
    transport = email_transport_status(db)
    if not transport["ready"]:
        raise HTTPException(status_code=503, detail="Email delivery is not configured. Check the backend email transport settings.")
    is_local = transport["transport"] == "local_log"
    try:
        send_email(
            recipient=admin.email,
            subject="RecliQ email configuration test",
            text_body="This test confirms the RecliQ email sender configuration. No account or reconciliation details are included.",
            log_body=False,
        )
    except Exception as exc:
        record_admin_event(
            db, request, actor_id=admin.id, action="platform.email_test_failed",
            target_type="platform_email_settings", target_id=EMAIL_SETTINGS_ID,
            metadata={"error_type": type(exc).__name__}, result="failed",
        )
        db.commit()
        raise HTTPException(status_code=503, detail="Test email could not be delivered. Check the SMTP configuration and try again.") from exc
    record_admin_event(
        db, request, actor_id=admin.id, action="platform.email_test_sent",
        target_type="platform_email_settings", target_id=EMAIL_SETTINGS_ID,
        metadata={"transport": str(transport["transport"])},
    )
    db.commit()
    if is_local:
        return {"message": "Test message was written to the backend log only; no email was sent.", "transport": "local_log"}
    return {"message": f"Test email sent to {admin.email}.", "transport": "smtp"}


@router.get("/audit")
def admin_audit(
    action: str | None = None,
    target_id: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    query = select(AdminAuditEvent)
    if action:
        query = query.where(AdminAuditEvent.action == action)
    if target_id:
        query = query.where(AdminAuditEvent.target_id == target_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    events = db.scalars(query.order_by(AdminAuditEvent.id.desc()).offset(offset).limit(limit)).all()
    return {"events": [{"sequence": item.id, "event_id": item.event_id, "occurred_at": item.occurred_at, "actor_id": item.actor_id, "action": item.action, "target_type": item.target_type, "target_id": item.target_id, "result": item.result, "before": _json(item.before_json, None), "after": _json(item.after_json, None), "metadata": _json(item.metadata_json, {})} for item in events], "total": total, "offset": offset, "limit": limit}


@router.get("/email-deliveries/summary")
def admin_email_delivery_summary(db: Session = Depends(get_db)) -> dict:
    counts = {name: 0 for name in ("queued", "processing", "sent", "failed")}
    for delivery_status, count in db.query(ReportEmailDelivery.status, func.count(ReportEmailDelivery.id)).group_by(ReportEmailDelivery.status):
        if delivery_status in counts:
            counts[delivery_status] = count
    return {
        "total": db.query(func.count(ReportEmailDelivery.id)).scalar() or 0,
        "by_status": counts,
        "updated_at": db.query(func.max(ReportEmailDelivery.updated_at)).scalar(),
    }


@router.get("/email-deliveries")
def admin_email_deliveries(
    delivery_status: str | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(ReportEmailDelivery)
    if delivery_status:
        if delivery_status not in {"queued", "processing", "sent", "failed"}:
            raise HTTPException(status_code=422, detail="Unknown email-delivery status.")
        query = query.filter(ReportEmailDelivery.status == delivery_status)
    total = query.count()
    rows = query.order_by(ReportEmailDelivery.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "deliveries": [{
            "id": item.id,
            "scope_id": item.scope_id,
            "job_id": item.job_id,
            "report_id": item.report_id,
            "recipients": _json(item.recipients_json, []),
            "status": item.status,
            "delivery_method": item.delivery_method,
            "transport": item.transport,
            "created_at": item.created_at,
            "sent_at": item.sent_at,
            "error_type": item.error_message.split("(", 1)[1].split(")", 1)[0] if item.error_message and "(" in item.error_message else None,
        } for item in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/audit/verify")
def verify_admin_audit(db: Session = Depends(get_db)) -> dict:
    previous = "0" * 64
    checked = 0
    for item in db.scalars(select(AdminAuditEvent).order_by(AdminAuditEvent.id)):
        occurred = item.occurred_at
        if occurred.tzinfo:
            occurred = occurred.astimezone(timezone.utc).replace(tzinfo=None)
        payload = {
            "event_id": item.event_id, "occurred_at": occurred.isoformat(timespec="microseconds"),
            "actor_id": item.actor_id, "action": item.action, "target_type": item.target_type,
            "target_id": item.target_id, "result": item.result, "ip_address": item.ip_address,
            "request_id": item.request_id, "before": item.before_json, "after": item.after_json,
            "metadata": item.metadata_json, "prev_hash": item.prev_hash,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        if item.prev_hash != previous or item.hash != digest:
            return {"valid": False, "checked": checked, "broken_at": item.event_id}
        previous = item.hash
        checked += 1
    return {"valid": True, "checked": checked, "broken_at": None}


@router.get("/health")
def system_health(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    health: dict[str, dict[str, str | int]] = {}
    try:
        db.execute(text("SELECT 1"))
        health["database"] = {"status": "healthy"}
    except Exception:
        health["database"] = {"status": "unavailable"}
    queued = db.query(func.count(ReconciliationJob.id)).filter(ReconciliationJob.status == "queued").scalar() or 0
    processing = db.query(func.count(ReconciliationJob.id)).filter(ReconciliationJob.status == "processing").scalar() or 0
    stale_before = datetime.now(timezone.utc) - timedelta(seconds=settings.job_lease_seconds)
    stale = db.query(func.count(ReconciliationJob.id)).filter(ReconciliationJob.status == "processing", ReconciliationJob.heartbeat_at < stale_before).scalar() or 0
    worker_count: int | None = None
    if settings.job_queue_backend.lower() == "redis":
        try:
            from app.jobs.queue import redis_connection, rq_queue
            from rq import Worker

            connection = redis_connection()
            connection.ping()
            worker_count = len(Worker.all(queue=rq_queue(connection)))
            health["redis"] = {"status": "healthy"}
        except Exception:
            worker_count = 0
            health["redis"] = {"status": "unavailable"}
    else:
        health["redis"] = {"status": "not_configured"}
    health["jobs"] = {
        "status": "degraded" if stale or worker_count == 0 else "healthy",
        "queued": queued,
        "processing": processing,
        "stale": stale,
        **({"workers": worker_count} if worker_count is not None else {}),
    }
    try:
        storage = get_storage()
        if getattr(storage, "name", "local") == "local":
            health["storage"] = {"status": "healthy" if storage.base_path.exists() else "unavailable"}
        else:
            health["storage"] = {"status": "healthy" if storage.container.exists() else "unavailable"}
    except Exception:
        health["storage"] = {"status": "unavailable"}
    email_ready = bool(email_transport_status(db)["smtp_ready"])
    health["email"] = {"status": "ready" if email_ready else "not_configured"}
    from app.core import secret_box

    health["mfa_encryption"] = {"status": "healthy" if secret_box.encryption_enabled() else "not_configured"}
    health["api"] = {"status": "healthy"}
    return {"checked_at": datetime.now(timezone.utc), "services": health}
