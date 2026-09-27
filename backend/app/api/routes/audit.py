import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_actor, get_session_id
from app.database.session import get_db
from app.services.audit_service import (
    AuditActor,
    event_to_dict,
    export_csv,
    list_events,
    record_event,
    verify_chain,
)

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/events")
def audit_events(
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> dict:
    events = list_events(db, session_id, action=action, entity_type=entity_type, entity_id=entity_id, limit=limit, offset=offset)
    return {"events": [event_to_dict(event) for event in events], "limit": limit, "offset": offset}


@router.get("/verify")
def verify_audit_chain(db: Session = Depends(get_db), session_id: str = Depends(get_session_id)) -> dict:
    """Recompute the hash chain: any edited or removed event is reported."""
    return verify_chain(db, session_id)


@router.get("/export")
def export_audit_log(
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    entity_id: str | None = None,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> Response:
    events = list(reversed(list_events(db, session_id, entity_id=entity_id)))  # Oldest first.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    record_event(
        scope_id=session_id, actor=actor, action="audit.exported", entity_type="audit_log",
        summary=f"Exported {len(events)} audit event(s) as {format.upper()}", metadata={"entity_id": entity_id},
    )
    if format == "json":
        body = json.dumps(
            {"exported_at": stamp, "verification": verify_chain(db, session_id), "events": [event_to_dict(event) for event in events]},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        return Response(body, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="RecliQ_Audit_Log_{stamp}.json"'})
    return Response(
        "﻿" + export_csv(events),  # BOM so Excel opens UTF-8 correctly.
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="RecliQ_Audit_Log_{stamp}.csv"'},
    )
