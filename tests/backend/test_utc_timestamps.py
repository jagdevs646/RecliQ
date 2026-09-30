import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.main import app
from app.database.session import SessionLocal
from app.models.job import ReconciliationJob
from app.utils.timestamps import iso_utc


def test_job_timestamps_in_api_carry_a_utc_offset():
    stored = datetime(2026, 9, 30, 19, 19, 50, 472435)  # naive, as SQLite returns it
    with TestClient(app) as client:
        session_id = client.get("/api/session").json()["session_id"]
        db = SessionLocal()
        job = ReconciliationJob(
            session_id=session_id,
            job_type="generic",
            status="completed",
            progress=100,
            orientation="vertical",
            settings_json="{}",
            created_at=stored,
            completed_at=stored + timedelta(seconds=5),
        )
        db.add(job)
        db.commit()
        job_id = job.id
        try:
            body = client.get(f"/api/jobs/{job_id}").json()
            listed = next(item for item in client.get("/api/jobs").json()["jobs"] if item["id"] == job_id)
        finally:
            db.query(ReconciliationJob).filter(ReconciliationJob.id == job_id).delete()
            db.commit()
            db.close()

    for payload in (body, listed):
        created = datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00"))
        assert created.utcoffset() == timedelta(0), payload["created_at"]
        assert created == stored.replace(tzinfo=timezone.utc)
        completed = datetime.fromisoformat(payload["completed_at"].replace("Z", "+00:00"))
        assert completed.utcoffset() == timedelta(0)
        assert payload["started_at"] is None


def test_iso_utc_marks_naive_values_as_utc_and_converts_others():
    assert iso_utc(None) is None
    assert iso_utc(datetime(2026, 9, 30, 19, 19, 50)) == "2026-09-30T19:19:50+00:00"
    ist = timezone(timedelta(hours=5, minutes=30))
    assert iso_utc(datetime(2026, 10, 1, 0, 49, 50, tzinfo=ist)) == "2026-09-30T19:19:50+00:00"
