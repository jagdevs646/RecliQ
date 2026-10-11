from __future__ import annotations

import json
import re
import smtplib

import pytest
from fastapi.testclient import TestClient

from app.database.session import SessionLocal
from app.models.account import PlatformPolicy, ReportEmailDelivery
from app.models.job import ReconciliationJob
from app.models.report import Report
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
