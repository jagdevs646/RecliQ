import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.api.deps import get_actor, get_session_id
from app.core.anonymous_session import websocket_session_id
from app.database.session import SessionLocal, get_db
from app.models.job import ReconciliationJob
from app.schemas.job import JobListResponse, ReconciliationJobOut
from app.storage import get_storage
from app.services.audit_service import AuditActor, record_event
from app.services.job_service import cancel_job, clear_all_jobs, delete_job, get_job, list_jobs

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=JobListResponse)
def jobs(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> JobListResponse:
    return JobListResponse(jobs=list_jobs(db, session_id, limit=limit))


@router.delete("", status_code=status.HTTP_200_OK)
def clear_history(
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> dict[str, int]:
    storage = get_storage()
    job_ids = [job.id for job in list_jobs(db, session_id, limit=10_000)]
    deleted_count = clear_all_jobs(db, session_id, storage=storage)
    record_event(
        scope_id=session_id, actor=actor, action="history.cleared", entity_type="session", entity_id=session_id,
        summary=f"Deleted {deleted_count} reconciliation(s)", before={"job_ids": job_ids},
    )
    return {"deleted_count": deleted_count}


@router.get("/{job_id}", response_model=ReconciliationJobOut)
def job_detail(
    job_id: str,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> ReconciliationJob:
    job = get_job(db, job_id, session_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.post("/{job_id}/cancel", response_model=ReconciliationJobOut)
def cancel_reconciliation_job(
    job_id: str,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> ReconciliationJob:
    existing = get_job(db, job_id, session_id)
    previous_status = existing.status if existing else None
    job = cancel_job(db, job_id, session_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if previous_status != job.status:
        record_event(
            scope_id=session_id, actor=actor, action="job.cancelled", entity_type="job", entity_id=job_id,
            summary="Reconciliation cancelled", before={"status": previous_status}, after={"status": job.status},
        )
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reconciliation_job(
    job_id: str,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> None:
    storage = get_storage()
    existing = get_job(db, job_id, session_id)
    before = (
        {"status": existing.status, "job_type": existing.job_type, "report_id": existing.report_id,
         "created_at": existing.created_at.isoformat() if existing.created_at else None}
        if existing else None
    )
    success = delete_job(db, job_id, session_id, storage=storage)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    record_event(
        scope_id=session_id, actor=actor, action="job.deleted", entity_type="job", entity_id=job_id,
        summary="Reconciliation deleted", before=before,
    )


@router.websocket("/{job_id}/ws")
async def job_progress_websocket(websocket: WebSocket, job_id: str):
    session_id = websocket_session_id(websocket)
    await websocket.accept()
    last_progress = -1
    last_status = ""
    try:
        while True:
            db = SessionLocal()
            try:
                job = get_job(db, job_id, session_id)
                if job:
                    if job.progress != last_progress or job.status != last_status:
                        last_progress = job.progress
                        last_status = job.status
                        await websocket.send_json({
                            "id": job.id,
                            "job_type": job.job_type,
                            "status": job.status,
                            "progress": job.progress,
                            "error_message": job.error_message,
                            "input_file_1_name": job.input_file_1_name,
                            "input_file_2_name": job.input_file_2_name,
                            "report_id": job.report_id,
                            "attempts": job.attempts,
                        })
                    if job.status in {"completed", "completed_with_errors", "failed", "cancelled"}:
                        break
            finally:
                db.close()
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
