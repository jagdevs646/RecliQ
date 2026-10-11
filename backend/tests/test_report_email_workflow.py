from __future__ import annotations

import json
import re
import smtplib

import pyotp
import pytest
from fastapi.testclient import TestClient

from app.database.session import SessionLocal
from app.models.account import PlatformPolicy, ReportEmailDelivery, User
from app.models.job import ReconciliationJob
from app.models.report import Report
from app.services.account_security import hash_password
from app.storage import get_storage


def _enable_guest_report_email() -> None:
    db = SessionLocal()
    try:
        policy = db.get(PlatformPolicy, "global")
        features = json.loads(policy.feature_flags_json)
        features["report_email"] = True
        policy.feature_flags_json = json.dumps(features)
        limits = json.loads(policy.limits_json)
        limits["guest"]["max_email_recipients"] = 1
        limits["guest"]["report_emails_per_day"] = 1
        policy.limits_json = json.dumps(limits)
        db.commit()
    finally:
        db.close()


def _create_guest_report(session_id: str, tmp_path) -> str:
    report_path = f"reports/{session_id}/reconciliation.xlsx"
    source = tmp_path / "reconciliation.xlsx"
    source.write_bytes(b"test-workbook-bytes")
    get_storage().save_file(source, report_path)

    db = SessionLocal()
    try:
        report = Report(
            session_id=session_id,
            filename="reconciliation.xlsx",
            storage_backend="local",
            storage_path=report_path,
            size_bytes=source.stat().st_size,
            summary_json=json.dumps({"matched_records": 3, "only_in_file_1": 1}),
        )
        db.add(report)
        db.flush()
        job = ReconciliationJob(
            session_id=session_id,
            job_type="generic",
            display_name="Monthly reconciliation",
            status="completed",
            progress=100,
            orientation="vertical",
            settings_json="{}",
            report_id=report.id,
        )
        db.add(job)
        db.commit()
        return job.id
    finally:
        db.close()


def _login_test_admin(client: TestClient) -> None:
    secret = pyotp.random_base32()
    db = SessionLocal()
    try:
        user = User(
            username="deliveryadmin",
            username_key="deliveryadmin",
            email="deliveryadmin@example.test",
            email_key="deliveryadmin@example.test",
            display_name="Delivery Admin",
            password_hash=hash_password("email-admin-test-password-8721"),
            role="super_admin",
            status="active",
            email_verified=True,
            mfa_enabled=True,
            mfa_secret=secret,
        )
        db.add(user)
        db.commit()
    finally:
        db.close()
    response = client.post("/api/auth/login", json={
        "identifier": "deliveryadmin",
        "password": "email-admin-test-password-8721",
        "totp_code": pyotp.TOTP(secret).now(),
    })
    assert response.status_code == 200, response.text


def test_admin_email_delivery_summary_and_history_are_paginated(client: TestClient, tmp_path):
    session_id = client.get("/api/session").json()["session_id"]
    job_id = _create_guest_report(session_id, tmp_path)
    db = SessionLocal()
    try:
        report_id = db.query(ReconciliationJob).filter_by(id=job_id).one().report_id
        for index, delivery_status in enumerate(("queued", "processing", "sent", "failed", "sent")):
            db.add(ReportEmailDelivery(
                scope_id=session_id,
                job_id=job_id,
                report_id=report_id,
                recipients_json=json.dumps([f"recipient{index}@example.test"]),
                subject="Reconciliation report",
                idempotency_key=f"admin-delivery-test-{index}",
                delivery_method="secure_link",
                status=delivery_status,
                transport="smtp" if delivery_status == "sent" else None,
            ))
        db.commit()
    finally:
        db.close()

    assert client.get("/api/admin/email-deliveries/summary").status_code == 401
    _login_test_admin(client)

    summary_response = client.get("/api/admin/email-deliveries/summary")
    assert summary_response.status_code == 200, summary_response.text
    summary = summary_response.json()
    assert summary["total"] == 5
    assert summary["by_status"] == {"queued": 1, "processing": 1, "sent": 2, "failed": 1}
    assert summary["updated_at"]

    page = client.get("/api/admin/email-deliveries?status=failed&offset=0&limit=1")
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 1
    assert page.json()["deliveries"][0]["recipients"] == ["recipient3@example.test"]


def test_guest_email_report_uses_secure_revocable_link_and_idempotency(client: TestClient, tmp_path, monkeypatch):
    from app.jobs import email_runner

    session_id = client.get("/api/session").json()["session_id"]
    job_id = _create_guest_report(session_id, tmp_path)
    _enable_guest_report_email()
    sent_messages = []

    def capture_email(**kwargs):
        sent_messages.append(kwargs)
        return "local-log"

    monkeypatch.setattr(email_runner, "send_email", capture_email)
    payload = {
        "recipients": ["reviewer@example.com"],
        "subject": "Monthly reconciliation report",
        "message": "Please review this report.",
        "include_summary": True,
        "delivery_method": "secure_link",
        "idempotency_key": "recliq-report-email-0001",
    }

    recipient_limit = client.post("/api/reports/job/{}/email".format(job_id), json={
        **payload,
        "recipients": ["reviewer@example.com", "other@example.com"],
    })
    assert recipient_limit.status_code == 429
    assert recipient_limit.json()["detail"]["code"] == "email_recipient_limit_exceeded"

    response = client.post(f"/api/reports/job/{job_id}/email", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()[0]["status"] == "sent"
    assert response.json()[0]["transport"] == "local_log"
    assert len(sent_messages) == 1
    assert sent_messages[0]["recipient"] == "reviewer@example.com"

    duplicate = client.post(f"/api/reports/job/{job_id}/email", json=payload)
    assert duplicate.status_code == 200
    assert duplicate.json()[0]["id"] == response.json()[0]["id"]
    assert len(sent_messages) == 1

    delivery_id = response.json()[0]["id"]
    link = re.search(r"/api/reports/shared/([A-Za-z0-9_-]+)", sent_messages[0]["text_body"])
    assert link
    token = link.group(1)
    download = client.get(f"/api/reports/shared/{token}")
    assert download.status_code == 200
    assert download.content == b"test-workbook-bytes"

    revoked = client.delete(f"/api/reports/email-deliveries/{delivery_id}/link")
    assert revoked.status_code == 204
    assert client.get(f"/api/reports/shared/{token}").status_code == 404

    over_quota = client.post(f"/api/reports/job/{job_id}/email", json={
        **payload,
        "idempotency_key": "recliq-report-email-0002",
    })
    assert over_quota.status_code == 429
    assert over_quota.json()["detail"]["code"] == "email_quota_exceeded"

    db = SessionLocal()
    try:
        delivery = db.query(ReportEmailDelivery).filter_by(id=delivery_id).one()
        assert delivery.status == "sent"
        assert delivery.scope_id == session_id
    finally:
        db.close()


def test_smtp_recipient_refusal_is_not_reported_as_sent(monkeypatch):
    from app.core.config import get_settings
    from app.services.email_service import send_email

    settings = get_settings()
    monkeypatch.setattr(settings, "email_backend", "smtp")
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from_email", "reports@example.com")
    monkeypatch.setattr(settings, "smtp_starttls", False)
    refused = {"recipient@example.com": (550, b"mailbox unavailable")}

    class FakeSMTP:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def ehlo(self):
            return None

        def send_message(self, _message):
            return refused

    monkeypatch.setattr(smtplib, "SMTP", lambda *_args, **_kwargs: FakeSMTP())
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        send_email(
            recipient="recipient@example.com",
            subject="Report",
            text_body="A report is ready.",
            log_body=False,
        )
