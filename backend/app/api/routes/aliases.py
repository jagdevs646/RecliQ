from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_actor, get_session_id
from app.database.session import get_db
from app.models.alias import EntityAlias, MatchDecision
from app.reconciliation_engine.normalization.entities import EntityNormalizer
from app.services import alias_service
from app.services.audit_service import AuditActor

router = APIRouter(prefix="/aliases", tags=["aliases"])


class AliasCreate(BaseModel):
    canonical: str = Field(min_length=1, max_length=300)
    variants: list[str] = Field(min_length=1)
    column_hint: str = ""


class DecisionCreate(BaseModel):
    value_1: str = Field(min_length=1, max_length=300)
    value_2: str = Field(min_length=1, max_length=300)
    decision: str = Field(pattern="^(accept|reject)$")
    job_id: str | None = None
    column_hint: str = ""
    confidence: int | None = Field(default=None, ge=0, le=100)
    note: str = ""


class SuggestionApproval(BaseModel):
    canonical: str
    variant: str
    column_hint: str = ""


class NormalizationPreview(BaseModel):
    value_1: str
    value_2: str


def _alias_out(alias: EntityAlias) -> dict:
    return {
        "id": alias.id,
        "canonical": alias.canonical,
        "variant": alias.variant,
        "column_hint": alias.column_hint,
        "active": alias.active,
        "source": alias.source,
        "created_at": alias.created_at.isoformat() if alias.created_at else None,
    }


@router.get("")
def list_aliases(include_inactive: bool = False, db: Session = Depends(get_db), session_id: str = Depends(get_session_id)) -> dict:
    query = db.query(EntityAlias).filter(EntityAlias.scope_id == session_id)
    if not include_inactive:
        query = query.filter(EntityAlias.active.is_(True))
    return {"aliases": [_alias_out(alias) for alias in query.order_by(EntityAlias.canonical).all()]}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_aliases(
    payload: AliasCreate,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> dict:
    created = []
    try:
        for variant in payload.variants:
            created.append(
                alias_service.create_alias(
                    db, scope_id=session_id, actor=actor, canonical=payload.canonical, variant=variant, column_hint=payload.column_hint
                )
            )
    except alias_service.AliasConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"aliases": [_alias_out(alias) for alias in created]}


@router.delete("/{alias_id}")
def remove_alias(
    alias_id: str,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> dict:
    alias = alias_service.deactivate_alias(db, scope_id=session_id, actor=actor, alias_id=alias_id)
    if alias is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alias not found")
    return _alias_out(alias)


@router.post("/decisions", status_code=status.HTTP_201_CREATED)
def record_decision(
    payload: DecisionCreate,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> dict:
    try:
        decision = alias_service.record_decision(db, scope_id=session_id, actor=actor, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": decision.id, "decision": decision.decision, "pair_key": decision.pair_key}


@router.get("/decisions")
def list_decisions(job_id: str | None = None, db: Session = Depends(get_db), session_id: str = Depends(get_session_id)) -> dict:
    query = db.query(MatchDecision).filter(MatchDecision.scope_id == session_id)
    if job_id:
        query = query.filter(MatchDecision.job_id == job_id)
    return {
        "decisions": [
            {
                "id": row.id, "job_id": row.job_id, "value_1": row.value_1, "value_2": row.value_2,
                "decision": row.decision, "confidence": row.confidence, "column_hint": row.column_hint,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in query.order_by(MatchDecision.created_at.desc()).limit(500).all()
        ]
    }


@router.get("/suggestions")
def alias_suggestions(db: Session = Depends(get_db), session_id: str = Depends(get_session_id)) -> dict:
    return {
        "minimum_acceptances": alias_service.MIN_ACCEPTANCES,
        "suggestions": [suggestion.__dict__ for suggestion in alias_service.suggestions(db, session_id)],
    }


@router.post("/suggestions/approve", status_code=status.HTTP_201_CREATED)
def approve_suggestion(
    payload: SuggestionApproval,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> dict:
    key = alias_service.pair_key(payload.canonical, payload.variant)
    if key not in {suggestion.pair_key for suggestion in alias_service.suggestions(db, session_id)}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This pair is not a current suggestion")
    try:
        alias = alias_service.create_alias(
            db, scope_id=session_id, actor=actor, canonical=payload.canonical, variant=payload.variant,
            column_hint=payload.column_hint, source="suggestion",
        )
    except alias_service.AliasConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _alias_out(alias)


@router.post("/preview")
def preview_normalization(payload: NormalizationPreview, db: Session = Depends(get_db), session_id: str = Depends(get_session_id)) -> dict:
    """Show how two values normalize, with this organization's aliases."""
    from app.services.reconciliation_service import _saved_aliases
    from app.reconciliation_engine.normalization.entities import NormalizerConfig

    normalizer = EntityNormalizer(NormalizerConfig(aliases=tuple(_saved_aliases(db, session_id))))
    return {
        "value_1": {"input": payload.value_1, "normalized": normalizer.canonical(payload.value_1).text},
        "value_2": {"input": payload.value_2, "normalized": normalizer.canonical(payload.value_2).text},
        "equivalent": normalizer.equivalent(payload.value_1, payload.value_2),
        "rules": normalizer.explain(payload.value_1, payload.value_2),
    }
