"""Claiming, heartbeats and error classification for one job execution."""
from __future__ import annotations

import logging
import os
import socket
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import update

from app.core.observability import bind_context
from app.database.session import SessionLocal, engine
from app.models.job import ReconciliationJob

logger = logging.getLogger("recliq.jobs")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
_ENGINE_PID = os.getpid()


def worker_id() -> str:
    # A forked RQ work-horse has its own PID; name it separately.
    return f"{socket.gethostname()}:{os.getpid()}"


def claim_job(job_id: str) -> bool:
    """Atomically move a queued job to processing. Only one worker wins."""
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        result = db.execute(
            update(ReconciliationJob)
            .where(ReconciliationJob.id == job_id, ReconciliationJob.status == "queued")
            .values(
                status="processing",
                attempts=ReconciliationJob.attempts + 1,
                worker_id=worker_id(),
                heartbeat_at=now,
                started_at=now,
                progress=10,
                error_message=None,
            )
        )
        db.commit()
        return result.rowcount == 1
    finally:
        db.close()


@contextmanager
def heartbeat(job_id: str, interval_seconds: float) -> Iterator[None]:
    """Refresh the job's heartbeat while it runs, so the sweeper can tell a
    long-running job from one whose worker died."""
    stop = threading.Event()
    owner = worker_id()

    def beat() -> None:
        while not stop.wait(interval_seconds):
            db = SessionLocal()
            try:
                db.execute(
                    update(ReconciliationJob)
                    .where(
                        ReconciliationJob.id == job_id,
                        ReconciliationJob.status == "processing",
                        ReconciliationJob.worker_id == owner,
                    )
                    .values(heartbeat_at=datetime.now(timezone.utc))
                )
                db.commit()
            except Exception:  # A missed beat is retried on the next tick.
                db.rollback()
                logger.warning("Heartbeat update failed", exc_info=True)
            finally:
                db.close()

    thread = threading.Thread(target=beat, name=f"heartbeat-{job_id[:8]}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


def is_transient(exc: BaseException) -> bool:
    """Infrastructure failures worth retrying; data and configuration errors
    are not (they would fail the same way again)."""
    from sqlalchemy.exc import DBAPIError, OperationalError

    if isinstance(exc, OperationalError) or (isinstance(exc, DBAPIError) and exc.connection_invalidated):
        return True
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    try:
        from azure.core.exceptions import ServiceRequestError, ServiceResponseError

        if isinstance(exc, (ServiceRequestError, ServiceResponseError)):
            return True
    except ImportError:  # pragma: no cover
        pass
    try:
        from redis.exceptions import ConnectionError as RedisConnectionError

        return isinstance(exc, RedisConnectionError)
    except ImportError:  # pragma: no cover
        return False


def run_job(job_id: str) -> None:
    """Queue entry point: run one reconciliation job with its log context."""
    if os.getpid() != _ENGINE_PID:
        # A forked worker must not reuse the parent's pooled connections.
        engine.dispose(close=False)
    from app.services.reconciliation_service import process_reconciliation_job

    with bind_context(job_id=job_id):
        logger.info("Job picked up by worker", extra={"worker_id": worker_id()})
        process_reconciliation_job(job_id)
