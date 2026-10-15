from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pyotp
from fastapi.testclient import TestClient

from app.database.session import SessionLocal
from app.models.account import PlatformPolicy, ReportEmailDelivery, User, UserPreference, UserSession
from app.models.file import UploadedFile
from app.models.job import ReconciliationJob
from app.models.report import Report
from app.services.account_security import hash_password, token_digest
from app.storage import get_storage


def create_user(*, username: str = "reviewer", role: str = "user", mfa: bool = False) -> User:
    db = SessionLocal()
    try:
        secret = pyotp.random_base32() if mfa else None
        user = User(
            username=username,
            username_key=username.casefold(),
            email=f"{username}@example.com",
            email_key=f"{username}@example.com",
            display_name=f"{username.title()} User",
            password_hash=hash_password("strong-test-password-8742"),
            role=role,
            status="active",
            email_verified=True,
            mfa_enabled=mfa,
            mfa_secret=secret,
        )
        db.add(user)
        db.flush()
        db.add(UserPreference(user_id=user.id))
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def login(client: TestClient, user: User, *, totp: bool = False, password: str = "strong-test-password-8742") -> dict:
    payload = {"identifier": user.username, "password": password}
    if totp:
        payload["totp_code"] = pyotp.TOTP(user.mfa_secret).now()
    response = client.post("/api/auth/login", json=payload)
    assert response.status_code == 200, response.text
    identity = client.get("/api/auth/me")
    assert identity.status_code == 200
    return identity.json()


def add_job(scope_id: str, name: str) -> str:
    db = SessionLocal()
    try:
        job = ReconciliationJob(session_id=scope_id, job_type="generic", display_name=name, status="failed")
        db.add(job)
        db.commit()
        db.refresh(job)
        return job.id
    finally:
        db.close()


def update_platform_policy(*, features: dict | None = None, limits: dict | None = None) -> None:
    db = SessionLocal()
    try:
        policy = db.get(PlatformPolicy, "global")
        if features is not None:
            current = json.loads(policy.feature_flags_json)
            current.update(features)
            policy.feature_flags_json = json.dumps(current)
        if limits is not None:
            current = json.loads(policy.limits_json)
            for group, values in limits.items():
                current[group].update(values)
            policy.limits_json = json.dumps(current)
        db.commit()
    finally:
        db.close()


def test_guest_sessions_are_isolated(client: TestClient):
    guest_a = client.get("/api/session").json()["session_id"]
    job_a = add_job(guest_a, "Private guest reconciliation")
    assert [job["id"] for job in client.get("/api/jobs").json()["jobs"]] == [job_a]

    with TestClient(client.app) as other_browser:
        guest_b = other_browser.get("/api/session").json()["session_id"]
        assert guest_b != guest_a
        assert other_browser.get("/api/jobs").json()["jobs"] == []
        overview = other_browser.get("/api/workspace/overview").json()
        assert overview["metrics"]["runs"] == 0


def test_login_uses_backend_identifier_contract_and_csrf_protection(client: TestClient):
    user = create_user()
    identity = login(client, user)
    assert identity["authenticated"] is True
    assert identity["workspace"]["id"] == f"user-{user.id}"
    cookie = client.cookies.get("recliq_auth")
    assert cookie

    profile = {"display_name": "Updated Reviewer"}
    blocked = client.put("/api/auth/profile", json=profile)
    assert blocked.status_code == 403
    csrf = client.get("/api/auth/me").headers.get("x-csrf-token")
    assert csrf
    updated = client.put("/api/auth/profile", json=profile, headers={"X-CSRF-Token": csrf})
    assert updated.status_code == 200
    assert updated.json()["user"]["display_name"] == "Updated Reviewer"


def test_auth_middleware_handles_activity_refresh_without_detached_user_error(client: TestClient):
    user = create_user(username="activityrefresh")
    login(client, user)
    token = client.cookies.get("recliq_auth")
    assert token

    db = SessionLocal()
    try:
        session = db.query(UserSession).filter_by(token_hash=token_digest(token)).one()
        session.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        db.commit()
    finally:
        db.close()

    response = client.get("/api/auth/me")
    assert response.status_code == 200, response.text
    assert response.json()["authenticated"] is True


def test_password_reset_revokes_old_sessions_and_consumes_token(client: TestClient):
    from app.api.routes.auth import _new_auth_token

    user = create_user()
    login(client, user)
    db = SessionLocal()
    try:
        raw_token = _new_auth_token(db, user, "password_reset", ttl_hours=1)
        db.commit()
    finally:
        db.close()

    client.cookies.delete("recliq_auth")
    changed = client.post("/api/auth/password/reset", json={
        "token": raw_token,
        "new_password": "replacement-strong-password-9918",
    })
    assert changed.status_code == 200, changed.text
    assert client.get("/api/auth/me").json()["authenticated"] is False
    assert client.post("/api/auth/login", json={
        "identifier": user.username,
        "password": "replacement-strong-password-9918",
    }).status_code == 200

    client.cookies.delete("recliq_auth")
    reused = client.post("/api/auth/password/reset", json={
        "token": raw_token,
        "new_password": "another-strong-password-2219",
    })
    assert reused.status_code == 400


def test_local_password_reset_returns_a_working_link_only_for_existing_account(client: TestClient, caplog):
    user = create_user(username="resetlink")
    response = client.post("/api/auth/password/forgot", json={"email": user.email})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["message"] == "Local password reset link ready. No email was sent."
    parsed = urlparse(result["development_reset_url"])
    token = parse_qs(parsed.query)["token"][0]
    assert parsed.path == "/reset-password"
    assert token not in caplog.text

    unknown = client.post("/api/auth/password/forgot", json={"email": "missing@example.com"})
    assert unknown.status_code == 200
    assert unknown.json() == {"message": "If an account exists for that email, password reset instructions have been sent."}

    completed = client.post("/api/auth/password/reset", json={
        "token": token,
        "new_password": "reset-link-new-password-9918",
    })
    assert completed.status_code == 200, completed.text
    assert client.post("/api/auth/login", json={
        "identifier": user.username,
        "password": "reset-link-new-password-9918",
    }).status_code == 200


def test_smtp_password_reset_keeps_generic_response_and_hides_development_link(client: TestClient, monkeypatch):
    from app.api.routes import auth
    from app.core.config import get_settings

    user = create_user(username="smtpreset")
    monkeypatch.setattr(get_settings(), "email_backend", "smtp")
    monkeypatch.setattr(auth, "_send_action_email", lambda **kwargs: None)

    response = client.post("/api/auth/password/forgot", json={"email": user.email})
    assert response.status_code == 200, response.text
    assert response.json() == {"message": "If an account exists for that email, password reset instructions have been sent."}


def test_admin_can_change_sender_and_test_local_email_transport(client: TestClient):
    admin = create_user(username="emailadmin", role="super_admin", mfa=True)
    login(client, admin, totp=True)
    csrf = client.get("/api/auth/me").headers["x-csrf-token"]

    settings = client.get("/api/admin/email-settings")
    assert settings.status_code == 200, settings.text
    assert settings.json()["sender_email"] == "jagdev.s65@gmail.com"
    assert settings.json()["transport"] == "local_log"

    updated = client.put(
        "/api/admin/email-settings",
        json={"sender_email": "recliq-notifications@example.com"},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["sender_email"] == "recliq-notifications@example.com"
    assert client.get("/api/admin/email-settings").json()["sender_email"] == "recliq-notifications@example.com"

    test_result = client.post("/api/admin/email-settings/test", headers={"X-CSRF-Token": csrf})
    assert test_result.status_code == 200, test_result.text
    assert test_result.json() == {
        "message": "Test message was written to the backend log only; no email was sent.",
        "transport": "local_log",
    }


def test_guest_workspace_import_requires_confirmation_and_is_one_time(client: TestClient, tmp_path):
    guest_id = client.get("/api/session").json()["session_id"]
    guest_job_id = add_job(guest_id, "Guest payroll run")
    source = tmp_path / "guest-report.xlsx"
    source.write_bytes(b"guest-report-data")
    guest_report_path = f"reports/{guest_id}/guest-report.xlsx"
    get_storage().save_file(source, guest_report_path)
    db = SessionLocal()
    try:
        report = Report(
            session_id=guest_id,
            filename="guest-report.xlsx",
            storage_backend="local",
            storage_path=guest_report_path,
            size_bytes=source.stat().st_size,
            summary_json="{}",
        )
        db.add(report)
        db.flush()
        guest_report_id = report.id
        job = db.get(ReconciliationJob, guest_job_id)
        job.report_id = report.id
        db.add(ReportEmailDelivery(
            scope_id=guest_id,
            job_id=guest_job_id,
            report_id=report.id,
            sender_user_id=None,
            recipients_json='["reviewer@example.com"]',
            subject="Guest report",
            message="",
            idempotency_key="guest-report-email-0001",
            delivery_method="secure_link",
            include_summary=True,
            status="sent",
            attempt_count=1,
            transport="local_log",
        ))
        db.commit()
    finally:
        db.close()

    user = create_user(username="importer")
    login(client, user)
    existing_job_id = add_job(f"user-{user.id}", "Existing account run")
    csrf = client.get("/api/auth/me").headers["x-csrf-token"]

    rejected = client.post("/api/auth/import-guest", json={"confirm": False}, headers={"X-CSRF-Token": csrf})
    assert rejected.status_code == 422
    imported = client.post("/api/auth/import-guest", json={"confirm": True}, headers={"X-CSRF-Token": csrf})
    assert imported.status_code == 200, imported.text
    assert imported.json()["counts"]["jobs"] == 1
    assert imported.json()["counts"]["report_emails"] == 1
    assert {job["id"] for job in client.get("/api/jobs").json()["jobs"]} == {guest_job_id, existing_job_id}
    overview = client.get("/api/workspace/overview?period=all").json()
    assert overview["usage"]["jobs_today"] == 2
    assert overview["usage"]["storage_bytes"] == source.stat().st_size
    db = SessionLocal()
    try:
        moved_email = db.query(ReportEmailDelivery).filter_by(idempotency_key="guest-report-email-0001").one()
        assert moved_email.scope_id == f"user-{user.id}"
        assert moved_email.sender_user_id == user.id
        moved_report = db.get(Report, guest_report_id)
        assert moved_report.session_id == f"user-{user.id}"
    finally:
        db.close()

    repeated = client.post("/api/auth/import-guest", json={"confirm": True}, headers={"X-CSRF-Token": csrf})
    assert repeated.status_code == 409


def test_guest_import_waits_for_active_reconciliation(client: TestClient):
    guest_id = client.get("/api/session").json()["session_id"]
    job_id = add_job(guest_id, "Still processing")
    db = SessionLocal()
    try:
        job = db.get(ReconciliationJob, job_id)
        job.status = "processing"
        db.commit()
    finally:
        db.close()

    user = create_user(username="activeimport")
    login(client, user)
    csrf = client.get("/api/auth/me").headers["x-csrf-token"]
    response = client.post("/api/auth/import-guest", json={"confirm": True}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 409
    assert "finish" in response.json()["detail"]
    db = SessionLocal()
    try:
        assert db.get(ReconciliationJob, job_id).session_id == guest_id
    finally:
        db.close()


def test_oauth_callback_rejects_missing_or_invalid_state_without_login(client: TestClient):
    response = client.get("/api/auth/oauth/google/callback?state=attacker-state", follow_redirects=False)
    assert response.status_code == 303
    assert "oauth_error=verification" in response.headers["location"]
    assert client.get("/api/auth/me").json()["authenticated"] is False


def test_oauth_signup_requires_provider_verified_email(client: TestClient, monkeypatch):
    import time

    from app.api.routes import auth as auth_routes

    state = "trusted-state-for-test"
    context = {
        "provider": "google",
        "state": state,
        "verifier": "test-verifier",
        "mode": "login",
        "user_id": None,
        "issued_at": int(time.time()),
    }

    class FakeResponse:
        def json(self):
            return {"sub": "provider-subject-123", "email": "unverified@example.com", "email_verified": False}

    class FakeOAuthClient:
        token = None

        def fetch_token(self, *_args, **_kwargs):
            return {"access_token": "test-only"}

        def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(auth_routes, "_oauth_config", lambda *_args: {
        "token_url": "https://provider.example/token",
        "userinfo_url": "https://provider.example/userinfo",
    })
    monkeypatch.setattr(auth_routes, "_oauth_client", lambda *_args: FakeOAuthClient())
    client.cookies.set(
        "recliq_oauth_google",
        auth_routes._encode_oauth_context(context),
        path="/api/auth/oauth/google",
    )

    response = client.get(
        f"/api/auth/oauth/google/callback?state={state}&code=not-a-real-code",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "oauth_error=unverified_email" in response.headers["location"]
    db = SessionLocal()
    try:
        assert db.query(User).filter_by(email_key="unverified@example.com").count() == 0
    finally:
        db.close()


def test_oauth_signup_requires_local_verification_when_provider_omits_claim(client: TestClient, monkeypatch):
    import time

    from app.api.routes import auth as auth_routes
    from app.models.account import AuthIdentity

    sent_email: dict = {}
    monkeypatch.setattr(auth_routes, "_send_action_email", lambda **kwargs: sent_email.update(kwargs))
    monkeypatch.setattr(auth_routes, "_oauth_config", lambda *_args: {
        "token_url": "https://provider.example/token",
        "userinfo_url": "https://provider.example/userinfo",
    })

    class FakeResponse:
        def json(self):
            return {"id": "facebook-user-123", "email": "facebook-user@example.com", "name": "Facebook User"}

    class FakeOAuthClient:
        token = None

        def fetch_token(self, *_args, **_kwargs):
            return {"access_token": "test-only"}

        def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(auth_routes, "_oauth_client", lambda *_args: FakeOAuthClient())
    state = "facebook-state-for-test"
    context = {
        "provider": "facebook", "state": state, "verifier": "test-verifier",
        "mode": "login", "user_id": None, "issued_at": int(time.time()),
    }
    client.cookies.set(
        "recliq_oauth_facebook",
        auth_routes._encode_oauth_context(context),
        path="/api/auth/oauth/facebook",
    )

    response = client.get(
        f"/api/auth/oauth/facebook/callback?state={state}&code=not-a-real-code",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "oauth=verification_sent" in response.headers["location"]
    assert client.get("/api/auth/me").json()["authenticated"] is False
    assert sent_email["recipient"] == "facebook-user@example.com"
    assert sent_email["route"] == "verify-email"

    db = SessionLocal()
    try:
        user = db.query(User).filter_by(email_key="facebook-user@example.com").one()
        user_id = user.id
        assert user.email_verified is False
        assert db.query(AuthIdentity).filter_by(user_id=user_id, provider="facebook").count() == 1
    finally:
        db.close()

    token = sent_email["token"]
    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 200
    client.cookies.set(
        "recliq_oauth_facebook",
        auth_routes._encode_oauth_context({**context, "state": "facebook-state-after-verification"}),
        path="/api/auth/oauth/facebook",
    )
    signed_in = client.get(
        "/api/auth/oauth/facebook/callback?state=facebook-state-after-verification&code=not-a-real-code",
        follow_redirects=False,
    )
    assert "oauth=success" in signed_in.headers["location"]
    assert client.get("/api/auth/me").json()["authenticated"] is True


def test_oauth_login_cannot_bypass_mfa_for_an_existing_account(client: TestClient, monkeypatch):
    import time

    from app.api.routes import auth as auth_routes
    from app.models.account import AuthIdentity

    user = create_user(username="mfauser", mfa=True)
    db = SessionLocal()
    try:
        db.add(AuthIdentity(user_id=user.id, provider="google", provider_subject="mfa-provider-subject", provider_email=user.email))
        db.commit()
    finally:
        db.close()

    state = "trusted-state-for-mfa-test"
    context = {
        "provider": "google", "state": state, "verifier": "test-verifier",
        "mode": "login", "user_id": None, "issued_at": int(time.time()),
    }

    class FakeResponse:
        def json(self):
            return {"sub": "mfa-provider-subject", "email": user.email, "email_verified": True}

    class FakeOAuthClient:
        token = None

        def fetch_token(self, *_args, **_kwargs):
            return {"access_token": "test-only"}

        def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(auth_routes, "_oauth_config", lambda *_args: {
        "token_url": "https://provider.example/token",
        "userinfo_url": "https://provider.example/userinfo",
    })
    monkeypatch.setattr(auth_routes, "_oauth_client", lambda *_args: FakeOAuthClient())
    client.cookies.set(
        "recliq_oauth_google",
        auth_routes._encode_oauth_context(context),
        path="/api/auth/oauth/google",
    )

    response = client.get(
        f"/api/auth/oauth/google/callback?state={state}&code=not-a-real-code",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "oauth_error=mfa_required" in response.headers["location"]
    assert client.get("/api/auth/me").json()["authenticated"] is False

    assert login(client, user, totp=True)["authenticated"] is True


def test_account_cookie_cannot_be_overridden_by_guest_session_header(client: TestClient):
    guest_id = client.get("/api/session").json()["session_id"]
    user = create_user()
    login(client, user)
    account_job = add_job(f"user-{user.id}", "Account-only run")
    add_job(guest_id, "Guest-only run")

    response = client.get("/api/jobs", headers={"X-Session-ID": guest_id})
    assert response.status_code == 200
    assert [job["id"] for job in response.json()["jobs"]] == [account_job]


def test_accounts_cannot_read_or_mutate_each_others_jobs_or_reports(client: TestClient, tmp_path):
    owner = create_user(username="privateowner")
    login(client, owner)
    report_source = tmp_path / "private-report.xlsx"
    report_source.write_bytes(b"private report contents")
    report_path = f"reports/user-{owner.id}/private-report.xlsx"
    get_storage().save_file(report_source, report_path)

    db = SessionLocal()
    try:
        report = Report(
            session_id=f"user-{owner.id}",
            filename="private-report.xlsx",
            storage_backend="local",
            storage_path=report_path,
            size_bytes=report_source.stat().st_size,
            summary_json='{"matched_records": 1}',
        )
        db.add(report)
        db.flush()
        job = ReconciliationJob(
            session_id=f"user-{owner.id}",
            job_type="generic",
            display_name="Private payroll run",
            status="completed",
            report_id=report.id,
        )
        db.add(job)
        db.commit()
        job_id, report_id = job.id, report.id
    finally:
        db.close()

    assert client.get(f"/api/jobs/{job_id}").status_code == 200
    assert client.get(f"/api/reports/{report_id}/download").content == b"private report contents"

    csrf = client.get("/api/auth/me").headers["x-csrf-token"]
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    other = create_user(username="otheraccount")
    login(client, other)
    csrf = client.get("/api/auth/me").headers["x-csrf-token"]

    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert client.get(f"/api/reports/{report_id}/download").status_code == 404
    assert client.get(f"/api/reports/job/{job_id}/summary").status_code == 404
    assert client.get(f"/api/reports/job/{job_id}/email").status_code == 404
    assert client.put(f"/api/jobs/{job_id}/name", json={"display_name": "Stolen"}, headers={"X-CSRF-Token": csrf}).status_code == 404
    assert client.delete(f"/api/jobs/{job_id}", headers={"X-CSRF-Token": csrf}).status_code == 404


def test_admin_usage_includes_report_and_preview_storage(client: TestClient):
    user = create_user(username="usageaccount")
    admin = create_user(username="usageadmin", role="super_admin", mfa=True)
    login(client, admin, totp=True)

    db = SessionLocal()
    try:
        report = Report(
            session_id=f"user-{user.id}",
            filename="usage-report.xlsx",
            storage_backend="local",
            storage_path=f"reports/user-{user.id}/usage-report.xlsx",
            size_bytes=13,
            data_size_bytes=17,
            summary_json="{}",
        )
        db.add(report)
        db.flush()
        db.add(UploadedFile(
            session_id=f"user-{user.id}",
            original_filename="source.xlsx",
            stored_filename="source.xlsx",
            storage_backend="local",
            storage_path=f"uploads/user-{user.id}/source.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            size_bytes=11,
        ))
        db.add(ReconciliationJob(session_id=f"user-{user.id}", job_type="generic", display_name="Usage run", status="completed", report_id=report.id))
        db.commit()
    finally:
        db.close()

    usage = client.get(f"/api/admin/users/{user.id}").json()["usage"]
    assert usage["uploaded_bytes"] == 11
    assert usage["report_bytes"] == 30
    assert usage["storage_bytes"] == 41


def test_gst_job_plan_restores_owned_files_and_configuration(client: TestClient):
    from app.models.file import UploadedFile

    owner = create_user(username="gstowner")
    login(client, owner)
    db = SessionLocal()
    try:
        source = UploadedFile(
            session_id=f"user-{owner.id}", original_filename="source.xlsx", stored_filename="source.xlsx",
            storage_backend="local", storage_path=f"uploads/user-{owner.id}/source.xlsx", size_bytes=10,
        )
        destination = UploadedFile(
            session_id=f"user-{owner.id}", original_filename="destination.xlsx", stored_filename="destination.xlsx",
            storage_backend="local", storage_path=f"uploads/user-{owner.id}/destination.xlsx", size_bytes=12,
        )
        db.add_all([source, destination])
        db.flush()
        source_id, destination_id = source.id, destination.id
        payload = {
            "file_1_id": source_id,
            "file_2_id": destination_id,
            "source_files_1": [{"file_id": source_id, "sheet_id": "Purchases"}],
            "source_files_2": [{"file_id": destination_id, "sheet_id": "GSTR2B"}],
            "orientation": "horizontal",
            "text_threshold": 92,
        }
        job = ReconciliationJob(
            session_id=f"user-{owner.id}", job_type="gst", display_name="GST run", status="completed",
            input_file_1_id=source_id, input_file_2_id=destination_id, orientation="horizontal",
            settings_json=json.dumps(payload),
        )
        db.add(job)
        db.commit()
        job_id = job.id
    finally:
        db.close()

    restored = client.get(f"/api/jobs/{job_id}/gst-plan")
    assert restored.status_code == 200, restored.text
    assert restored.json()["text_threshold"] == 92
    assert restored.json()["orientation"] == "horizontal"
    assert restored.json()["source_files_1"] == [{"file_id": source_id, "sheet_id": "Purchases"}]
    assert restored.json()["source_files_2"] == [{"file_id": destination_id, "sheet_id": "GSTR2B"}]
    assert {item["id"] for item in restored.json()["files"]} == {source_id, destination_id}

    csrf = client.get("/api/auth/me").headers["x-csrf-token"]
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    login(client, create_user(username="gstreader"))
    assert client.get(f"/api/jobs/{job_id}/gst-plan").status_code == 404


def test_public_registration_never_assigns_administrator(client: TestClient):
    response = client.post("/api/auth/register", json={
        "username": "jagdevs646",
        "email": "jagdevs646@gmail.com",
        "display_name": "New Member",
        "password": "very-strong-test-password-9182",
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert "development_verification_url" in body
    resent = client.post("/api/auth/verification/resend", json={"email": "jagdevs646@gmail.com"})
    assert resent.status_code == 200
    assert "development_verification_url" in resent.json()
    token = parse_qs(urlparse(resent.json()["development_verification_url"]).query)["token"][0]
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username_key="jagdevs646").one()
        assert user.role == "user"
        assert user.password_hash != "very-strong-test-password-9182"
        assert user.email_verified is False
    finally:
        db.close()
    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 200
    assert login(client, user, password="very-strong-test-password-9182")["authenticated"] is True


def test_registration_rejects_full_email_as_username(client: TestClient):
    response = client.post("/api/auth/register", json={
        "username": "jagdevs646@gmail.com",
        "email": "different@example.com",
        "display_name": "New Member",
        "password": "very-strong-test-password-9182",
    })
    assert response.status_code == 422
    assert "not your full email address" in response.text


def test_smtp_registration_does_not_return_verification_token(client: TestClient, monkeypatch):
    from app.api.routes import auth as auth_routes
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "email_backend", "smtp")
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_from_email", "recliq@example.test")
    monkeypatch.setattr(auth_routes, "_send_action_email", lambda **_kwargs: None)

    response = client.post("/api/auth/register", json={
        "username": "smtpmember",
        "email": "smtpmember@example.com",
        "display_name": "SMTP Member",
        "password": "very-strong-test-password-9182",
    })
    assert response.status_code == 201, response.text
    assert "development_verification_url" not in response.json()


def test_production_registration_creates_pending_account_without_smtp(client: TestClient, monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "email_backend", "log")

    response = client.post("/api/auth/register", json={
        "username": "prodmember",
        "email": "prodmember@example.com",
        "display_name": "Production Member",
        "password": "very-strong-test-password-9182",
    })
    assert response.status_code == 201, response.text
    assert response.json()["approval_required"] is True
    assert "administrator can approve" in response.json()["message"]
    assert "development_verification_url" not in response.json()
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username_key="prodmember").one()
        assert user.role == "user"
        assert user.email_verified is False
        assert user.email_verification_approved_by_admin is False
    finally:
        db.close()


def test_registration_smtp_failure_keeps_account_pending_for_admin_approval(client: TestClient, monkeypatch):
    from app.api.routes import auth as auth_routes
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "email_backend", "smtp")
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_from_email", "recliq@example.test")

    def fail_delivery(**_kwargs):
        raise OSError("network unavailable")

    monkeypatch.setattr(auth_routes, "_send_action_email", fail_delivery)
    response = client.post("/api/auth/register", json={
        "username": "smtpdownmember",
        "email": "smtpdownmember@example.com",
        "display_name": "SMTP Down Member",
        "password": "very-strong-test-password-9182",
    })
    assert response.status_code == 201, response.text
    assert response.json()["approval_required"] is True
    assert "development_verification_url" not in response.json()
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username_key="smtpdownmember").one()
        assert user.email_verified is False
        assert user.email_verification_approved_by_admin is False
    finally:
        db.close()


def test_super_admin_can_approve_one_pending_account_and_user_can_sign_in(client: TestClient, monkeypatch):
    from app.api.routes import auth as auth_routes
    from app.models.account import AdminAuditEvent, AuthToken

    monkeypatch.setattr(auth_routes, "_verification_email_ready", lambda _db: False)
    registered = client.post("/api/auth/register", json={
        "username": "approvalmember",
        "email": "approvalmember@example.com",
        "display_name": "Approval Member",
        "password": "very-strong-test-password-9182",
    })
    assert registered.status_code == 201, registered.text
    assert registered.json()["approval_required"] is True
    blocked_login = client.post("/api/auth/login", json={
        "identifier": "approvalmember",
        "password": "very-strong-test-password-9182",
    })
    assert blocked_login.status_code == 403
    assert "administrator approval" in blocked_login.json()["detail"]

    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username_key="approvalmember").one()
        approval_link = auth_routes._new_auth_token(db, user, "verify_email")
        db.commit()
        user_id = user.id
    finally:
        db.close()

    admin = create_user(username="approvaladmin", role="super_admin", mfa=True)
    login(client, admin, totp=True)
    csrf = client.get("/api/auth/me").headers["x-csrf-token"]
    missing_reason = client.post(
        f"/api/admin/users/{user_id}/approve-verification",
        json={"reason": "No"},
        headers={"X-CSRF-Token": csrf},
    )
    assert missing_reason.status_code == 422

    approved = client.post(
        f"/api/admin/users/{user_id}/approve-verification",
        json={"reason": "Confirmed this account request directly with the user."},
        headers={"X-CSRF-Token": csrf},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_by_admin"] is True
    assert approved.json()["email_verified"] is False
    details = client.get(f"/api/admin/users/{user_id}").json()["user"]
    assert details["email_verified"] is False
    assert details["email_verification_approved_by_admin"] is True
    assert details["email_verification_approved_by"] == admin.id
    assert details["email_verification_approved_at"]
    listing = client.get("/api/admin/users").json()
    listed_user = next(item for item in listing["users"] if item["id"] == user_id)
    assert listed_user["email_verification_approved_by_admin"] is True
    verification = client.post(
        "/api/auth/verify-email",
        json={"token": approval_link},
        headers={"X-CSRF-Token": csrf},
    )
    assert verification.status_code == 200, verification.text
    assert "Email verified" in verification.json()["message"]

    duplicate = client.post(
        f"/api/admin/users/{user_id}/approve-verification",
        json={"reason": "Repeated approval check."},
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate.status_code == 409
    db = SessionLocal()
    try:
        token = db.query(AuthToken).filter_by(user_id=user_id, purpose="verify_email").one()
        assert token.consumed_at is not None
        approved_user = db.get(User, user_id)
        assert approved_user.email_verified is True
        assert approved_user.email_verification_approved_by_admin is False
        event = db.query(AdminAuditEvent).filter_by(
            action="user.email_verification_approved", target_id=user_id,
        ).one()
        assert event.actor_id == admin.id
        assert json.loads(event.metadata_json)["reason"] == "Confirmed this account request directly with the user."
    finally:
        db.close()

    client.cookies.delete("recliq_auth")
    assert login(client, user, password="very-strong-test-password-9182")["authenticated"] is True


def test_verification_approval_is_super_admin_only_and_rejects_inactive_accounts(client: TestClient):
    user = create_user(username="approvaltarget")
    db = SessionLocal()
    try:
        pending = db.get(User, user.id)
        pending.email_verified = False
        pending.status = "suspended"
        db.commit()
    finally:
        db.close()

    ordinary_user = create_user(username="approvalcaller")
    login(client, ordinary_user)
    csrf = client.get("/api/auth/me").headers["x-csrf-token"]
    forbidden = client.post(
        f"/api/admin/users/{user.id}/approve-verification",
        json={"reason": "A valid reason for testing."},
        headers={"X-CSRF-Token": csrf},
    )
    assert forbidden.status_code == 403

    client.cookies.delete("recliq_auth")
    admin = create_user(username="approvalguard", role="super_admin", mfa=True)
    login(client, admin, totp=True)
    csrf = client.get("/api/auth/me").headers["x-csrf-token"]
    inactive = client.post(
        f"/api/admin/users/{user.id}/approve-verification",
        json={"reason": "The account is not active yet."},
        headers={"X-CSRF-Token": csrf},
    )
    assert inactive.status_code == 409

    own_account = client.post(
        f"/api/admin/users/{admin.id}/approve-verification",
        json={"reason": "Administrator self approval check."},
        headers={"X-CSRF-Token": csrf},
    )
    assert own_account.status_code == 409


def test_admin_routes_require_authenticated_mfa_enabled_super_admin(client: TestClient):
    guest_response = client.get("/api/admin/overview")
    assert guest_response.status_code == 401

    normal_user = create_user()
    login(client, normal_user)
    user_response = client.get("/api/admin/overview")
    assert user_response.status_code == 403

    csrf = client.get("/api/auth/me").headers["x-csrf-token"]
    client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    admin = create_user(username="platformadmin", role="super_admin", mfa=True)
    login(client, admin, totp=True)
    admin_response = client.get("/api/admin/overview")
    assert admin_response.status_code == 200, admin_response.text
    assert "accounts" in admin_response.json()


def test_bootstrap_secrets_create_mfa_admin_and_existing_email_is_not_promoted(client: TestClient, monkeypatch):
    from app.core.config import get_settings
    from app.services.platform_service import initialize_platform_state

    existing = create_user(username="existingmember")
    existing_admin = create_user(username="oldadmin", role="super_admin", mfa=True)
    settings = get_settings()
    secret = pyotp.random_base32()
    monkeypatch.setattr(settings, "bootstrap_admin_username", "localadmin")
    monkeypatch.setattr(settings, "bootstrap_admin_email", existing.email)
    monkeypatch.setattr(settings, "bootstrap_admin_password", "bootstrapped-admin-password-9927")
    monkeypatch.setattr(settings, "bootstrap_admin_totp_secret", secret)

    initialize_platform_state()
    db = SessionLocal()
    try:
        assert db.query(User).filter_by(username_key="localadmin").count() == 0
        assert db.query(User).filter_by(role="super_admin").count() == 1
        persisted_existing_admin = db.query(User).filter_by(username_key=existing_admin.username_key).one()
        assert persisted_existing_admin.id == existing_admin.id
    finally:
        db.close()

    monkeypatch.setattr(settings, "bootstrap_admin_email", "localadmin@example.com")
    initialize_platform_state()
    db = SessionLocal()
    try:
        assert db.query(User).filter_by(role="super_admin").count() == 2
        persisted_existing_admin = db.query(User).filter_by(username_key=existing_admin.username_key).one()
        assert persisted_existing_admin.id == existing_admin.id
        admin = db.query(User).filter_by(username_key="localadmin").one()
        assert admin.role == "super_admin"
        assert admin.mfa_enabled is True
        assert admin.email_verified is True
        original_hash = admin.password_hash
        original_secret = admin.mfa_secret
    finally:
        db.close()

    login(client, admin, totp=True, password="bootstrapped-admin-password-9927")
    assert client.get("/api/admin/overview").status_code == 200

    monkeypatch.setattr(settings, "bootstrap_admin_password", "changed-password-must-not-apply-2026")
    monkeypatch.setattr(settings, "bootstrap_admin_totp_secret", pyotp.random_base32())
    initialize_platform_state()
    db = SessionLocal()
    try:
        assert db.query(User).filter_by(role="super_admin").count() == 2
        persisted = db.query(User).filter_by(username_key="localadmin").one()
        assert persisted.password_hash == original_hash
        assert persisted.mfa_secret == original_secret
    finally:
        db.close()


def test_bootstrap_recognizes_same_admin_by_email_without_renaming_or_resetting(client: TestClient, monkeypatch):
    from unittest.mock import patch

    from app.core.config import get_settings
    from app.services.platform_service import initialize_platform_state

    existing_admin = create_user(username="establishedadmin", role="super_admin", mfa=True)
    original_hash = existing_admin.password_hash
    original_secret = existing_admin.mfa_secret
    settings = get_settings()
    monkeypatch.setattr(settings, "bootstrap_admin_username", "configured-admin-name")
    monkeypatch.setattr(settings, "bootstrap_admin_email", existing_admin.email)
    monkeypatch.setattr(settings, "bootstrap_admin_password", "strong-test-password-8742")
    monkeypatch.setattr(settings, "bootstrap_admin_totp_secret", original_secret)

    with patch("app.services.platform_service.logger.warning") as warning:
        initialize_platform_state()

    db = SessionLocal()
    try:
        assert db.query(User).filter_by(role="super_admin").count() == 1
        persisted = db.query(User).filter_by(id=existing_admin.id).one()
        assert persisted.username == "establishedadmin"
        assert persisted.password_hash == original_hash
        assert persisted.mfa_secret == original_secret
    finally:
        db.close()

    warning_args = [str(part) for call in warning.call_args_list for part in call.args]
    assert any("configured username differs" in part for part in warning_args)
    assert "strong-test-password-8742" not in " ".join(warning_args)
    assert original_secret not in " ".join(warning_args)
    email_login = client.post("/api/auth/login", json={
        "identifier": existing_admin.email,
        "password": "strong-test-password-8742",
        "totp_code": pyotp.TOTP(original_secret).now(),
    })
    assert email_login.status_code == 200, email_login.text
    assert client.get("/api/admin/overview").status_code == 200


def test_login_only_prompts_for_totp_after_valid_credentials_and_rejects_invalid_or_expired_codes(client: TestClient):
    admin = create_user(username="totpadmin", role="super_admin", mfa=True)
    bad_password = client.post("/api/auth/login", json={
        "identifier": admin.username,
        "password": "incorrect-test-password-0000",
    })
    assert bad_password.status_code == 401
    assert bad_password.json()["detail"] == "The username/email or password is incorrect."

    challenge = client.post("/api/auth/login", json={
        "identifier": admin.username,
        "password": "strong-test-password-8742",
    })
    assert challenge.status_code == 428
    assert challenge.json()["detail"] == "Enter your authenticator code to finish signing in."

    current_code = pyotp.TOTP(admin.mfa_secret).now()
    invalid_code = "000000" if current_code != "000000" else "000001"
    invalid = client.post("/api/auth/login", json={
        "identifier": admin.username,
        "password": "strong-test-password-8742",
        "totp_code": invalid_code,
    })
    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "The authenticator code is invalid or expired."

    expired_code = pyotp.TOTP(admin.mfa_secret).at(int(time.time()) - 180)
    expired = client.post("/api/auth/login", json={
        "identifier": admin.username,
        "password": "strong-test-password-8742",
        "totp_code": expired_code,
    })
    assert expired.status_code == 401
    assert expired.json()["detail"] == "The authenticator code is invalid or expired."

    from app.services.account_security import verify_totp

    previous_window_code = pyotp.TOTP(admin.mfa_secret).at(int(time.time()) - 30)
    assert verify_totp(admin, previous_window_code) is True

    assert login(client, admin, totp=True)["authenticated"] is True


def test_admin_health_reports_missing_redis_worker_as_degraded(client: TestClient, monkeypatch):
    from rq import Worker

    from app.core.config import get_settings
    from app.jobs import queue as queue_module

    admin = create_user(username="healthadmin", role="super_admin", mfa=True)
    login(client, admin, totp=True)
    monkeypatch.setattr(get_settings(), "job_queue_backend", "redis")

    class FakeRedis:
        def ping(self):
            return True

    monkeypatch.setattr(queue_module, "redis_connection", lambda: FakeRedis())
    monkeypatch.setattr(Worker, "all", classmethod(lambda _cls, **_kwargs: []))

    response = client.get("/api/admin/health")
    assert response.status_code == 200
    services = response.json()["services"]
    assert services["redis"]["status"] == "healthy"
    assert services["jobs"]["status"] == "degraded"
    assert services["jobs"]["workers"] == 0


def test_user_feature_override_cannot_reenable_globally_disabled_feature(client: TestClient):
    from app.models.account import UserPolicyOverride
    from app.services.policy_service import effective_features

    user = create_user()
    db = SessionLocal()
    try:
        policy = db.get(PlatformPolicy, "global")
        flags = json.loads(policy.feature_flags_json)
        flags["gst_reconciliation"] = False
        policy.feature_flags_json = json.dumps(flags)
        db.add(UserPolicyOverride(user_id=user.id, feature_overrides_json=json.dumps({"gst_reconciliation": True}), limits_overrides_json="{}"))
        db.commit()
        assert effective_features(db, user)["gst_reconciliation"] is False
    finally:
        db.close()


def test_account_limit_overrides_can_exceed_defaults_but_respect_hard_caps(client: TestClient):
    from app.models.account import UserPolicyOverride
    from app.services.policy_service import effective_limits

    user = create_user()
    update_platform_policy(limits={"registered": {"max_file_size_mb": 25, "max_rows_per_sheet": 100_000, "allowed_formats": [".xlsx", ".csv"]}})
    db = SessionLocal()
    try:
        db.add(UserPolicyOverride(
            user_id=user.id,
            feature_overrides_json="{}",
            limits_overrides_json=json.dumps({"max_file_size_mb": 40, "max_rows_per_sheet": 400_000, "allowed_formats": [".csv", ".pdf"]}),
        ))
        db.commit()
        limits = effective_limits(db, user)
        assert limits["max_file_size_mb"] == 40
        assert limits["max_rows_per_sheet"] == 400_000
        assert limits["allowed_formats"] == [".csv"]

        override = db.get(UserPolicyOverride, user.id)
        override.limits_overrides_json = json.dumps({"max_file_size_mb": 200})
        db.commit()
        assert effective_limits(db, user)["max_file_size_mb"] == 50
    finally:
        db.close()


def test_guest_report_link_is_not_accessible_without_valid_capability(client: TestClient):
    response = client.get("/api/reports/shared/not-a-valid-capability")
    assert response.status_code == 404


def test_upload_formats_policy_is_advertised_and_enforced(client: TestClient):
    update_platform_policy(limits={"guest": {"allowed_formats": [".xlsx"]}})

    formats = client.get("/api/files/formats")
    assert formats.status_code == 200
    assert [item["extension"] for item in formats.json()["formats"]] == [".xlsx"]

    rejected = client.post("/api/files/upload", files={"file": ("transactions.csv", b"id,amount\n1,10\n", "text/csv")})
    assert rejected.status_code == 415
    assert "not enabled" in rejected.json()["detail"]


def test_disabled_workspace_features_block_mutations(client: TestClient):
    update_platform_policy(features={"near_match_review": False, "auto_resolution": False, "saved_setups": False})

    alias_response = client.post("/api/aliases", json={"canonical": "Acme Ltd", "variants": ["Acme Limited"]})
    rule_response = client.post("/api/resolution-rules", json={
        "name": "Confirm exact match",
        "category": "to_confirm",
        "conditions": [{"operator": "confidence_gte", "value": 100}],
        "action": {"resolution": "Confirmed"},
    })
    template_response = client.post("/api/templates", json={"name": "Monthly run"})

    assert alias_response.status_code == 403
    assert rule_response.status_code == 403
    assert template_response.status_code == 403
    assert client.get("/api/auth/me").json()["features"]["saved_setups"] is False


def test_queued_job_rechecks_feature_policy_before_processing():
    from app.services.reconciliation_service import _process_claimed_job

    update_platform_policy(features={"basic_reconciliation": False})
    db = SessionLocal()
    try:
        job = ReconciliationJob(
            session_id="guest-worker-feature-check",
            job_type="generic",
            display_name="Must not run",
            status="processing",
            settings_json="{}",
        )
        db.add(job)
        db.commit()
        job_id = job.id
    finally:
        db.close()

    _process_claimed_job(job_id)

    db = SessionLocal()
    try:
        job = db.get(ReconciliationJob, job_id)
        assert job.status == "failed"
        assert "disabled before the job started" in job.error_message
    finally:
        db.close()
