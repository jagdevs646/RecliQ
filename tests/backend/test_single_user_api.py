import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.main import app
from app.database.session import SessionLocal
from app.models.job import ReconciliationJob


def test_jobs_are_available_without_authentication():
    with TestClient(app) as client:
        response = client.get("/api/jobs")
    assert response.status_code == 200
    assert "jobs" in response.json()


def test_auth_and_profile_routes_are_removed():
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={}).status_code == 404
        assert client.get("/api/auth/me").status_code == 404
        assert client.get("/api/profile").status_code == 404


def test_anonymous_sessions_isolate_jobs():
    with TestClient(app) as client_a, TestClient(app) as client_b:
        session_a = client_a.get("/api/session").json()["session_id"]
        session_b = client_b.get("/api/session").json()["session_id"]
        assert session_a != session_b

        db = SessionLocal()
        job = ReconciliationJob(
            session_id=session_a,
            job_type="generic",
            status="queued",
            progress=0,
            orientation="vertical",
            settings_json="{}",
        )
        db.add(job)
        db.commit()
        job_id = job.id
        try:
            assert len(client_a.get("/api/jobs").json()["jobs"]) >= 1
            assert client_b.get("/api/jobs").json()["jobs"] == []
            assert client_b.get(f"/api/jobs/{job_id}").status_code == 404
        finally:
            db.query(ReconciliationJob).filter(ReconciliationJob.id == job_id).delete()
            db.commit()
            db.close()
