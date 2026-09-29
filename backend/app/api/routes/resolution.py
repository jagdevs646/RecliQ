from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_actor, get_session_id
from app.api.routes.reports import _job_report, _report_preview_data
from app.database.session import get_db
from app.reconciliation_engine.resolution import (
    CATEGORIES,
    CONFIRM_OPERATORS,
    DIFFERENCE_OPERATORS,
    NUMBER_OPERATORS,
    TEXT_OPERATORS,
    ALLOWED_OPERATORS,
)
from app.services import learning_service, resolution_service
from app.services.audit_service import AuditActor

router = APIRouter(prefix="/resolution-rules", tags=["auto-resolution"])


class RuleCondition(BaseModel):
    operator: str
    column: str = ""
    value: Any = None
    value_2: Any = None


class RuleAction(BaseModel):
    resolution: str = Field(min_length=1, max_length=120)
    reason_code: str = Field(default="", max_length=40)
    gl_account: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=500)


class RuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    category: str
    conditions: list[RuleCondition] = Field(min_length=1)
    action: RuleAction
    enabled: bool = True
    priority: int | None = None


class RuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    category: str | None = None
    conditions: list[RuleCondition] | None = None
    action: RuleAction | None = None
    enabled: bool | None = None
    priority: int | None = None


class RulePreview(BaseModel):
    job_id: str
    rule: RuleCreate


class RuleOrder(BaseModel):
    rule_ids: list[str]


def _unprocessable(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.get("/catalog")
def catalog() -> dict:
    """Exception types and the conditions each one allows, for the rule editor."""
    groups = {"text": TEXT_OPERATORS, "number": NUMBER_OPERATORS, "difference": DIFFERENCE_OPERATORS, "confirmation": CONFIRM_OPERATORS}
    return {
        "categories": [{"id": key, "label": label} for key, label in CATEGORIES.items()],
        "operators": [
            {"id": operator, "label": label, "group": group, "categories": [c for c, allowed in ALLOWED_OPERATORS.items() if operator in allowed]}
            for group, operators in groups.items()
            for operator, label in operators.items()
        ],
    }


@router.get("")
def list_rules(include_archived: bool = False, db: Session = Depends(get_db), session_id: str = Depends(get_session_id)) -> dict:
    return {"rules": [resolution_service.rule_out(rule) for rule in resolution_service.list_rules(db, session_id, include_archived)]}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_rule(
    payload: RuleCreate,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> dict:
    try:
        rule = resolution_service.create_rule(db, scope_id=session_id, actor=actor, payload=payload.model_dump())
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    return resolution_service.rule_out(rule)


@router.put("/{rule_id}")
def update_rule(
    rule_id: str,
    payload: RuleUpdate,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> dict:
    try:
        rule = resolution_service.update_rule(
            db, scope_id=session_id, actor=actor, rule_id=rule_id, payload=payload.model_dump(exclude_unset=True)
        )
    except resolution_service.RuleNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found") from exc
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    return resolution_service.rule_out(rule)


@router.delete("/{rule_id}")
def archive_rule(
    rule_id: str,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> dict:
    try:
        rule = resolution_service.archive_rule(db, scope_id=session_id, actor=actor, rule_id=rule_id)
    except resolution_service.RuleNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found") from exc
    return resolution_service.rule_out(rule)


@router.post("/order")
def reorder(
    payload: RuleOrder,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> dict:
    try:
        rules = resolution_service.reorder_rules(db, scope_id=session_id, actor=actor, rule_ids=payload.rule_ids)
    except ValueError as exc:
        raise _unprocessable(exc) from exc
    return {"rules": [resolution_service.rule_out(rule) for rule in rules]}


@router.post("/preview")
def preview_rule(payload: RulePreview, db: Session = Depends(get_db), session_id: str = Depends(get_session_id)) -> dict:
    """What the rule would resolve in a finished reconciliation (nothing is changed)."""
    _, report = _job_report(db, payload.job_id, session_id)
    data = _report_preview_data(report)
    try:
        return resolution_service.preview(payload.rule.model_dump(), data, learning_service.build_context(db, session_id))
    except ValueError as exc:
        raise _unprocessable(exc) from exc


@router.get("/suggestions")
def suggestions(db: Session = Depends(get_db), session_id: str = Depends(get_session_id)) -> dict:
    """Recurring exceptions and learned patterns that a rule could handle."""
    return {
        "minimum_reconciliations": resolution_service.MIN_JOBS_FOR_SUGGESTION,
        "suggestions": resolution_service.pattern_suggestions(db, session_id)
        + [item for item in learning_service.suggestions(db, session_id) if item.get("rule")],
    }
