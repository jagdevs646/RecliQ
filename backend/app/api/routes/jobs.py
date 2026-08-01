import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.api.deps import get_session_id
from app.core.anonymous_session import websocket_session_id
from app.database.session import SessionLocal, get_db
from app.models.job import ReconciliationJob
from app.schemas.job import JobListResponse, ReconciliationJobOut
from app.services.job_service import get_job, list_jobs

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=JobListResponse)
def jobs(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
) -> JobListResponse:
    return JobListResponse(jobs=list_jobs(db, session_id, limit=limit))


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
                        })
                    if job.status in {"completed", "failed"}:
                        break
            finally:
                db.close()
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
