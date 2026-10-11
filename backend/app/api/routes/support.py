from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_optional_user, get_session_id, require_admin
from app.database.session import get_db
from app.models.account import User
from app.models.support import SupportTicket, SupportTicketMessage
from app.services.account_security import record_admin_event
from app.services.audit_service import AuditActor, record_event


router = APIRouter(tags=["support"])

CATEGORIES = {
    "account_access", "file_upload", "reconciliation", "matching_and_mapping",
    "results_and_reports", "saved_setups", "aliases", "auto_resolution",
    "learning", "performance", "privacy_security", "other",
}
STAGES = {
    "dashboard", "upload", "sheet_pairing", "matching_key", "column_mapping",
    "report_setup", "running", "results", "history", "account_access", "settings", "other",
}
IMPACTS = {"blocked", "incorrect_results", "unable_to_complete", "question", "visual_issue"}
STATUSES = {"new", "in_progress", "waiting_for_user", "resolved"}
PRIORITIES = {"low", "normal", "high", "urgent"}


class TicketCreate(BaseModel):
    category: str
    custom_area: str = Field(default="", max_length=120)
    workflow_stage: str
    impact: str
    subject: str = Field(min_length=5, max_length=160)
    description: str = Field(min_length=10, max_length=6000)
    expected_behavior: str = Field(default="", max_length=3000)
    reproduction_steps: str = Field(default="", max_length=5000)

    @field_validator("subject", "description")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field is required.")
        return value

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        if value not in CATEGORIES:
            raise ValueError("Choose a supported support category.")
        return value

    @field_validator("workflow_stage")
    @classmethod
    def validate_stage(cls, value: str) -> str:
        if value not in STAGES:
            raise ValueError("Choose a supported workflow stage.")
        return value

    @field_validator("impact")
    @classmethod
    def validate_impact(cls, value: str) -> str:
        if value not in IMPACTS:
            raise ValueError("Choose a supported impact level.")
        return value


class MessageCreate(BaseModel):
    body: str = Field(min_length=2, max_length=6000)

    @field_validator("body")
    @classmethod
    def strip_body(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A message is required.")
        return value


class TicketUpdate(BaseModel):
    status: Literal["new", "in_progress", "waiting_for_user", "resolved"] | None = None
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    reply: str | None = Field(default=None, min_length=2, max_length=6000)

    @field_validator("reply")
    @classmethod
    def strip_reply(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("A reply cannot be empty.")
        return value


def _message_json(message: SupportTicketMessage) -> dict:
    return {
        "id": message.id,
        "author_type": message.author_type,
        "author_label": message.author_label,
        "body": message.body,
        "created_at": message.created_at,
    }


def _ticket_json(ticket: SupportTicket, *, user: User | None = None, include_messages: bool = False, db: Session | None = None) -> dict:
    result = {
        "id": ticket.id,
        "category": ticket.category,
        "custom_area": ticket.custom_area,
        "workflow_stage": ticket.workflow_stage,
        "impact": ticket.impact,
        "subject": ticket.subject,
        "description": ticket.description,
        "expected_behavior": ticket.expected_behavior,
        "reproduction_steps": ticket.reproduction_steps,
        "status": ticket.status,
        "priority": ticket.priority,
        "requester_type": ticket.requester_type,
        "requester": {
            "user_id": user.id if user else None,
            "username": user.username if user else None,
            "display_name": user.display_name if user else "Guest workspace",
            "email": user.email if user else None,
            "scope_id": ticket.scope_id,
        },
        "created_at": ticket.created_at,
        "updated_at": ticket.updated_at,
        "admin_read_at": ticket.admin_read_at,
    }
    if include_messages and db is not None:
        messages = db.scalars(select(SupportTicketMessage).where(SupportTicketMessage.ticket_id == ticket.id).order_by(SupportTicketMessage.created_at, SupportTicketMessage.id)).all()
        result["messages"] = [_message_json(message) for message in messages]
    return result


@router.get("/support")
def list_my_tickets(
    scope_id: str = Depends(get_session_id),
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
) -> dict:
    query = select(SupportTicket).where(SupportTicket.scope_id == scope_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    tickets = db.scalars(query.order_by(SupportTicket.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"tickets": [_ticket_json(ticket) for ticket in tickets], "total": total, "page": page, "page_size": page_size}


@router.post("/support", status_code=201)
def create_ticket(
    payload: TicketCreate,
    request: Request,
    scope_id: str = Depends(get_session_id),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> dict:
    if payload.category == "other" and not payload.custom_area.strip():
        raise HTTPException(status_code=422, detail="Describe the area for an Other complaint.")
    if payload.category != "other" and payload.custom_area.strip():
        raise HTTPException(status_code=422, detail="Custom area is only available for Other.")

    now = datetime.now(timezone.utc)
    recent = db.scalar(
        select(func.count(SupportTicket.id)).where(
            SupportTicket.scope_id == scope_id,
            SupportTicket.created_at >= now - timedelta(hours=1),
        )
    ) or 0
    if recent >= 5:
        raise HTTPException(status_code=429, detail="Too many support requests were created recently. Try again later.")

    ticket = SupportTicket(
        scope_id=scope_id,
        requester_user_id=user.id if user else None,
        requester_type="account" if user else "guest",
        category=payload.category,
        custom_area=payload.custom_area.strip(),
        workflow_stage=payload.workflow_stage,
        impact=payload.impact,
        subject=payload.subject,
        description=payload.description,
        expected_behavior=payload.expected_behavior.strip(),
        reproduction_steps=payload.reproduction_steps.strip(),
    )
    db.add(ticket)
    db.flush()
    db.commit()
    db.refresh(ticket)
    record_event(
        scope_id=scope_id,
        actor=AuditActor.from_request(request, scope_id),
        action="support.ticket_created",
        entity_type="support_ticket",
        entity_id=ticket.id,
        summary="Support request submitted",
        after={"category": ticket.category, "workflow_stage": ticket.workflow_stage, "impact": ticket.impact},
    )
    return _ticket_json(ticket, user=user)


@router.get("/support/{ticket_id}")
def get_my_ticket(ticket_id: str, scope_id: str = Depends(get_session_id), db: Session = Depends(get_db)) -> dict:
    ticket = db.scalar(select(SupportTicket).where(SupportTicket.id == ticket_id, SupportTicket.scope_id == scope_id))
    if not ticket:
        raise HTTPException(status_code=404, detail="Support request not found.")
    user = db.get(User, ticket.requester_user_id) if ticket.requester_user_id else None
    return _ticket_json(ticket, user=user, include_messages=True, db=db)


@router.post("/support/{ticket_id}/messages", status_code=201)
def reply_to_my_ticket(
    ticket_id: str,
    payload: MessageCreate,
    request: Request,
    scope_id: str = Depends(get_session_id),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> dict:
    ticket = db.scalar(select(SupportTicket).where(SupportTicket.id == ticket_id, SupportTicket.scope_id == scope_id))
    if not ticket:
        raise HTTPException(status_code=404, detail="Support request not found.")
    message = SupportTicketMessage(
        ticket_id=ticket.id,
        author_type="user",
        author_user_id=user.id if user else None,
        author_label=user.display_name if user else "You",
        body=payload.body,
    )
    ticket.status = "new"
    ticket.admin_read_at = None
    ticket.updated_at = datetime.now(timezone.utc)
    db.add(message)
    db.flush()
    db.commit()
    db.refresh(message)
    record_event(
        scope_id=scope_id,
        actor=AuditActor.from_request(request, scope_id),
        action="support.ticket_message_added",
        entity_type="support_ticket",
        entity_id=ticket.id,
        summary="A message was added to a support request",
        after={"author_type": "user"},
    )
    return _message_json(message)


@router.get("/admin/complaints/summary")
def complaint_summary(db: Session = Depends(get_db), _admin: User = Depends(require_admin)) -> dict:
    grouped = db.execute(
        select(SupportTicket.category, func.count(SupportTicket.id))
        .where(SupportTicket.status != "resolved")
        .group_by(SupportTicket.category)
    ).all()
    priority_counts = dict(db.execute(select(SupportTicket.priority, func.count(SupportTicket.id)).where(SupportTicket.status != "resolved").group_by(SupportTicket.priority)).all())
    return {
        "total": db.scalar(select(func.count(SupportTicket.id))) or 0,
        "unresolved": db.scalar(select(func.count(SupportTicket.id)).where(SupportTicket.status != "resolved")) or 0,
        "unread": db.scalar(select(func.count(SupportTicket.id)).where(SupportTicket.admin_read_at.is_(None))) or 0,
        "new": db.scalar(select(func.count(SupportTicket.id)).where(SupportTicket.status == "new")) or 0,
        "urgent": priority_counts.get("urgent", 0),
        "high": priority_counts.get("high", 0),
        "by_category": {category: count for category, count in grouped},
        "by_priority": priority_counts,
    }


@router.get("/admin/complaints")
def list_all_tickets(
    category: str | None = None,
    ticket_status: str | None = Query(default=None, alias="status"),
    priority: str | None = None,
    user_id: str | None = None,
    q: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> dict:
    query = select(SupportTicket, User).outerjoin(User, User.id == SupportTicket.requester_user_id)
    if category:
        if category not in CATEGORIES:
            raise HTTPException(status_code=422, detail="Unknown complaint category.")
        query = query.where(SupportTicket.category == category)
    if ticket_status:
        if ticket_status not in STATUSES:
            raise HTTPException(status_code=422, detail="Unknown complaint status.")
        query = query.where(SupportTicket.status == ticket_status)
    if priority:
        if priority not in PRIORITIES:
            raise HTTPException(status_code=422, detail="Unknown complaint priority.")
        query = query.where(SupportTicket.priority == priority)
    if user_id:
        query = query.where(SupportTicket.requester_user_id == user_id)
    if q.strip():
        term = f"%{q.strip().casefold()}%"
        query = query.where(or_(func.lower(SupportTicket.subject).like(term), func.lower(SupportTicket.description).like(term), func.lower(SupportTicket.custom_area).like(term), func.lower(User.username).like(term), func.lower(User.email).like(term), User.id.like(term)))
    count_query = select(func.count()).select_from(query.subquery())
    total = db.scalar(count_query) or 0
    rows = db.execute(query.order_by(SupportTicket.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    tickets = [_ticket_json(ticket, user=user) for ticket, user in rows]
    return {"tickets": tickets, "total": total, "page": page, "page_size": page_size}


@router.get("/admin/complaints/{ticket_id}")
def get_ticket_as_admin(ticket_id: str, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    ticket = db.get(SupportTicket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Complaint not found.")
    user = db.get(User, ticket.requester_user_id) if ticket.requester_user_id else None
    if ticket.admin_read_at is None:
        ticket.admin_read_at = datetime.now(timezone.utc)
        db.commit()
    return _ticket_json(ticket, user=user, include_messages=True, db=db)


@router.patch("/admin/complaints/{ticket_id}")
def update_ticket_as_admin(
    ticket_id: str,
    payload: TicketUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict:
    if payload.status is None and payload.priority is None and payload.reply is None:
        raise HTTPException(status_code=422, detail="Make at least one complaint update.")
    ticket = db.get(SupportTicket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Complaint not found.")
    before = {"status": ticket.status, "priority": ticket.priority}
    if payload.status is not None:
        ticket.status = payload.status
    if payload.priority is not None:
        ticket.priority = payload.priority
    if payload.reply is not None:
        db.add(SupportTicketMessage(ticket_id=ticket.id, author_type="admin", author_user_id=admin.id, author_label=admin.display_name, body=payload.reply))
        if payload.status is None:
            ticket.status = "waiting_for_user"
    ticket.updated_at = datetime.now(timezone.utc)
    db.add(ticket)
    record_admin_event(
        db,
        request,
        actor_id=admin.id,
        action="support.complaint_updated",
        target_type="support_ticket",
        target_id=ticket.id,
        before=before,
        after={"status": ticket.status, "priority": ticket.priority, "reply_added": payload.reply is not None},
        metadata={"category": ticket.category, "requester_user_id": ticket.requester_user_id},
    )
    db.flush()
    db.flush()
    db.commit()
    db.refresh(ticket)
    record_event(
        scope_id=ticket.scope_id,
        actor=AuditActor("user", admin.id, request.client.host if request.client else None, (request.headers.get("user-agent") or "")[:300] or None),
        action="support.complaint_updated",
        entity_type="support_ticket",
        entity_id=ticket.id,
        summary="An administrator updated a support request",
        before=before,
        after={"status": ticket.status, "priority": ticket.priority, "reply_added": payload.reply is not None},
    )
    user = db.get(User, ticket.requester_user_id) if ticket.requester_user_id else None
    return _ticket_json(ticket, user=user, include_messages=True, db=db)


@router.post("/admin/complaints/mark-read")
def mark_complaints_read(db: Session = Depends(get_db), _admin: User = Depends(require_admin)) -> dict:
    now = datetime.now(timezone.utc)
    result = db.query(SupportTicket).filter(SupportTicket.admin_read_at.is_(None)).update({SupportTicket.admin_read_at: now}, synchronize_session=False)
    db.commit()
    return {"marked_read": result}
