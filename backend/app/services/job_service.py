from sqlalchemy.orm import Session

from app.models.history import ReconciliationHistory
from app.models.job import ReconciliationJob


def list_jobs(db: Session, session_id: str, limit: int = 50) -> list[ReconciliationJob]:
    return (
        db.query(ReconciliationJob)
        .filter(ReconciliationJob.session_id == session_id)
        .order_by(ReconciliationJob.created_at.desc())
        .limit(limit)
        .all()
    )


def get_job(db: Session, job_id: str, session_id: str) -> ReconciliationJob | None:
    return db.query(ReconciliationJob).filter(ReconciliationJob.id == job_id, ReconciliationJob.session_id == session_id).first()


def append_history(db: Session, job: ReconciliationJob, event_type: str, message: str, metadata_json: str = "{}") -> None:
    db.add(
        ReconciliationHistory(
            session_id=job.session_id,
            job_id=job.id,
            event_type=event_type,
            message=message,
            metadata_json=metadata_json,
        )
    )
