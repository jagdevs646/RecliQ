from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_session_id
from app.database.session import get_db
from app.reconciliation_engine.learning import MIN_EVIDENCE
from app.services import alias_service, learning_service

router = APIRouter(prefix="/learning", tags=["learning"])


@router.get("")
def overview(db: Session = Depends(get_db), session_id: str = Depends(get_session_id)) -> dict:
    """What reviewers' decisions have taught RecliQ, with the evidence."""
    context = learning_service.build_context(db, session_id)
    return {
        "minimum_evidence": MIN_EVIDENCE,
        "method_weights": learning_service.method_weights(context),
        "remembered_rejections": learning_service.remembered_rejections(db, session_id),
        "suggestions": learning_service.suggestions(db, session_id),
        "alias_suggestions": len(alias_service.suggestions(db, session_id)),
        "confirmed_pairs": sum(1 for history in context.pairs.values() if history.accepted and not history.rejected),
    }
