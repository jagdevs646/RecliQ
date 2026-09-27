"""Durable job execution: claiming, recovery after restarts, retries, RQ."""
from datetime import datetime, timedelta, timezone

import fakeredis
import pytest
from fastapi.testclient import TestClient
from rq import SimpleWorker
from rq.timeouts import TimerDeathPenalty

from _helpers import single_rule_plan, upload, wait_for_job
from app.core.config import get_settings
from app.database.session import SessionLocal
from app.jobs import queue as job_queue
from app.jobs.recovery import recover_orphaned_jobs
from app.jobs.runner import claim_job, is_transient
from app.main import app
from app.models.history import ReconciliationHistory
from app.models.job import ReconciliationJob


def _job(status: str, **fields) -> str:
    with SessionLocal() as db:
        job = ReconciliationJob(session_id="00000000-0000-4000-8000-000000000001", job_type="generic", status=status,
                                progress=0, orientation="vertical", settings_json="{}", **fields)
        db.add(job)
        db.commit()
        return job.id


def _status(job_id: str) -> ReconciliationJob:
    with SessionLocal() as db:
        job = db.get(ReconciliationJob, job_id)
        db.expunge(job)
        return job


@pytest.fixture
def captured_enqueues(monkeypatch):
    enqueued: list[str] = []
    monkeypatch.setattr(job_queue, "enqueue_job", enqueued.append)
    return enqueued


def test_only_one_worker_can_claim_a_job():
    job_id = _job("queued")
    assert claim_job(job_id) is True
    assert claim_job(job_id) is False  # Already processing.
    job = _status(job_id)
    assert job.status == "processing" and job.attempts == 1 and job.heartbeat_at is not None
    assert claim_job(_job("cancelled")) is False


def test_stale_processing_job_is_retried_then_failed_at_the_limit(captured_enqueues):
    stale = datetime.now(timezone.utc) - timedelta(hours=1)
    retry_id = _job("processing", attempts=1, heartbeat_at=stale, started_at=stale)
    exhausted_id = _job("processing", attempts=get_settings().job_max_attempts, heartbeat_at=stale, started_at=stale)
    fresh_id = _job("processing", attempts=1, heartbeat_at=datetime.now(timezone.utc))

    counts = recover_orphaned_jobs()

    assert counts["retried"] >= 1 and counts["failed"] >= 1
    assert _status(retry_id).status == "queued" and retry_id in captured_enqueues
    failed = _status(exhausted_id)
    assert failed.status == "failed" and "stopped responding" in failed.error_message
    assert _status(fresh_id).status == "processing"  # Still alive: untouched.
    with SessionLocal() as db:
        notes = [row.message for row in db.query(ReconciliationHistory).filter(ReconciliationHistory.job_id == retry_id)]
    assert any("retrying (attempt 2" in note for note in notes)


def test_queued_jobs_lost_by_a_restart_are_requeued(captured_enqueues):
    job_id = _job("queued")
    recover_orphaned_jobs(requeue_all_queued=True)
    assert job_id in captured_enqueues


def test_transient_errors_are_retried_but_data_errors_are_not():
    from sqlalchemy.exc import OperationalError

    assert is_transient(OperationalError("SELECT 1", {}, Exception("server closed the connection")))
    assert is_transient(ConnectionError("reset"))
    assert not is_transient(ValueError("Column not found"))
    assert not is_transient(KeyError("INVOICE"))


def test_redis_queue_runs_jobs_in_a_worker(monkeypatch):
    """The production path: API enqueues to Redis, an RQ worker runs the job."""
    server = fakeredis.FakeServer()
    monkeypatch.setattr(job_queue, "redis_connection", lambda: fakeredis.FakeStrictRedis(server=server))
    monkeypatch.setattr(get_settings(), "job_queue_backend", "redis")

    with TestClient(app) as client:
        source = upload(client, "a.xlsx", {"S": [{"Invoice": "I-1", "Amount": 5}]})
        destination = upload(client, "b.xlsx", {"D": [{"Invoice": "I1", "Amount": 5}]})
        plan = single_rule_plan(
            source, destination, "S", "D",
            matching_strategy={"primary_key_source": ["Invoice"], "primary_key_destination": ["Invoice"]},
            reconciliation_mapping=[{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}],
        )
        job = client.post("/api/reconciliation/generic", json=plan).json()
        assert job["status"] == "queued"
        assert job_queue.is_enqueued(job["id"])

        class Worker(SimpleWorker):
            death_penalty_class = TimerDeathPenalty

        connection = fakeredis.FakeStrictRedis(server=server)
        Worker([job_queue.rq_queue(connection)], connection=connection).work(burst=True)

        finished = wait_for_job(client, job["id"], timeout=10)
    assert finished["status"] == "completed", finished["error_message"]
    assert finished["attempts"] == 1


def test_readiness_reports_dependencies():
    with TestClient(app) as client:
        body = client.get("/health/ready").json()
    assert body["checks"]["database"] == "ok"
