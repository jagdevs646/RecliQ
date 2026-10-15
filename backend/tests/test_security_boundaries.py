from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings, get_settings
from app.database.session import SessionLocal
from app.models.job import ReconciliationJob
from app.services.account_security import check_auth_rate_limit, record_auth_attempt


def test_workspace_metrics_only_sum_completed_run_reports(client):
    session_id = client.get("/api/session").json()["session_id"]
    db = SessionLocal()
    try:
        db.add_all([
            ReconciliationJob(session_id=session_id, job_type="generic", status="completed", matched_records_count=7, exception_count=3, review_count=2),
            ReconciliationJob(session_id=session_id, job_type="generic", status="failed", matched_records_count=100, exception_count=200, review_count=300),
            ReconciliationJob(session_id=session_id, job_type="generic", status="processing", matched_records_count=1000, exception_count=2000, review_count=3000),
        ])
        db.commit()
    finally:
        db.close()

    metrics = client.get("/api/workspace/overview?period=all").json()["metrics"]
    assert metrics == {
        "runs": 3,
        "matched_records": 7,
        "exceptions_flagged": 3,
        "awaiting_review": 2,
        "completed_runs": 1,
        "failed_runs": 1,
        "active_runs": 1,
    }


def test_api_responses_include_security_headers_and_are_not_cached(client):
    response = client.get("/api/session")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


def test_production_requires_secure_cookies_and_a_strong_csrf_secret():
    with pytest.raises(ValueError, match="SESSION_COOKIE_SECURE"):
        Settings(environment="production", auth_csrf_secret="x" * 32)
    with pytest.raises(ValueError, match="AUTH_CSRF_SECRET"):
        Settings(environment="production", session_cookie_secure=True, auth_csrf_secret=None)
    settings = Settings(
        environment="production",
        session_cookie_secure=True,
        auth_csrf_secret="x" * 32,
        public_app_url="https://recliq.example.com",
    )
    assert settings.effective_auth_csrf_secret == "x" * 32


def test_production_requires_a_public_https_frontend_url():
    secure_settings = {"environment": "production", "session_cookie_secure": True, "auth_csrf_secret": "x" * 32}
    for url in ("http://localhost:5173", "https://localhost:5173", "http://recliq.example.com", "https://127.0.0.1"):
        with pytest.raises(ValueError, match="PUBLIC_APP_URL"):
            Settings(**secure_settings, public_app_url=url)

    settings = Settings(**secure_settings, public_app_url="https://recliq.example.com")
    assert settings.public_app_url == "https://recliq.example.com"


def test_auth_rate_limits_persist_per_action(monkeypatch):
    monkeypatch.setattr(get_settings(), "auth_max_attempts", 2)
    request = Request({
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "path": "/api/auth/register",
        "headers": [],
        "client": ("192.0.2.10", 12345),
        "server": ("testserver", 80),
    })
    db = SessionLocal()
    try:
        record_auth_attempt(db, request, None, action="registration")
        with pytest.raises(HTTPException) as limited:
            record_auth_attempt(db, request, None, action="registration")
        assert limited.value.status_code == 429
        with pytest.raises(HTTPException) as checked:
            check_auth_rate_limit(db, request, None, action="registration")
        assert checked.value.status_code == 429
        check_auth_rate_limit(db, request, None, action="verification_resend")
    finally:
        db.close()


def test_job_websocket_rejects_untrusted_origins_before_accepting(client):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/api/jobs/does-not-matter/ws", headers={"Origin": "https://attacker.example"}):
            pass
    assert closed.value.code == 1008


def test_job_websocket_accepts_configured_origin(client):
    with client.websocket_connect("/api/jobs/does-not-matter/ws", headers={"Origin": "http://testserver"}) as websocket:
        websocket.close()


def test_admin_policy_cannot_exceed_environment_hard_upload_caps():
    from app.api.routes.admin import _bounded_limits

    settings = get_settings()
    with pytest.raises(HTTPException) as file_limit:
        _bounded_limits({"max_file_size_mb": settings.max_upload_mb + 1})
    assert "max_file_size_mb" in str(file_limit.value.detail)
    with pytest.raises(HTTPException) as row_limit:
        _bounded_limits({"max_rows_per_sheet": settings.max_rows_per_sheet + 1})
    assert "max_rows_per_sheet" in str(row_limit.value.detail)
