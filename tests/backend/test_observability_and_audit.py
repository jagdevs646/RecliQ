"""Structured logs with request/job IDs, and the append-only audit log."""
import csv
import io
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from _helpers import single_rule_plan, upload, wait_for_job
from app.core.observability import JsonFormatter, RequestContextMiddleware, bind_context
from app.database.session import SessionLocal, engine
from app.main import app
from app.models.audit import AuditEvent


def test_json_logs_carry_request_and_job_ids():
    record = logging.LogRecord("recliq", logging.INFO, __file__, 1, "Job done %s", ("ok",), None)
    record.rows = 42
    with bind_context(request_id="req-1", job_id="job-9", session_id="sess"):
        payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "Job done ok"
    assert payload["request_id"] == "req-1" and payload["job_id"] == "job-9" and payload["session_id"] == "sess"
    assert payload["rows"] == 42 and payload["level"] == "INFO"


def test_request_id_is_echoed_and_errors_return_it():
    probe = FastAPI()
    probe.add_middleware(RequestContextMiddleware)

    @probe.get("/boom")
    def boom():
        raise RuntimeError("kaboom")

    with TestClient(probe, raise_server_exceptions=False) as client:
        response = client.get("/boom", headers={"X-Request-ID": "trace-123"})
    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "trace-123"
    assert response.json() == {"detail": "Internal server error", "request_id": "trace-123"}

    with TestClient(app) as client:
        generated = client.get("/api/jobs").headers["X-Request-ID"]
    assert len(generated) == 32


def test_actions_are_audited_with_before_and_after_values():
    with TestClient(app) as client:
        source = upload(client, "books.xlsx", {"S": [{"Invoice": "I-1", "Amount": 5}]})
        destination = upload(client, "bank.xlsx", {"D": [{"Invoice": "I1", "Amount": 5}]})
        plan = single_rule_plan(
            source, destination, "S", "D",
            matching_strategy={"primary_key_source": ["Invoice"], "primary_key_destination": ["Invoice"]},
            reconciliation_mapping=[{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}],
        )
        job = wait_for_job(client, client.post("/api/reconciliation/generic", json=plan).json()["id"])
        client.get(f"/api/reports/job/{job['id']}/download")
        client.delete(f"/api/jobs/{job['id']}")

        events = client.get("/api/audit/events").json()["events"]
        actions = [event["action"] for event in events]
        for expected in ("file.uploaded", "job.created", "job.completed", "report.downloaded", "job.deleted"):
            assert expected in actions, actions
        upload_event = next(event for event in events if event["action"] == "file.uploaded")
        assert upload_event["actor_type"] == "session" and len(upload_event["after"]["sha256"]) == 64
        deleted = next(event for event in events if event["action"] == "job.deleted")
        assert deleted["before"]["status"] == "completed"
        assert next(e for e in events if e["action"] == "job.completed")["actor_type"] == "system"

        assert client.get("/api/audit/verify").json()["valid"] is True

        exported = client.get("/api/audit/export", params={"format": "csv"})
        rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
        assert {"file.uploaded", "job.deleted"} <= {row["action"] for row in rows}
        as_json = client.get("/api/audit/export", params={"format": "json"}).json()
        assert as_json["verification"]["valid"] is True

        with TestClient(app) as other:
            assert other.get("/api/audit/events").json()["events"] == []  # Scoped per session.


def test_audit_events_cannot_be_modified_or_deleted():
    with TestClient(app) as client:
        upload(client, "x.xlsx", {"S": [{"A": 1}]})
        event_id = client.get("/api/audit/events").json()["events"][0]["event_id"]

    with SessionLocal() as db:
        event = db.query(AuditEvent).filter(AuditEvent.event_id == event_id).one()
        event.summary = "tampered"
        with pytest.raises(PermissionError):
            db.commit()
        db.rollback()
        with pytest.raises(PermissionError):
            db.delete(db.query(AuditEvent).filter(AuditEvent.event_id == event_id).one())
            db.commit()

    # Raw SQL is blocked by the database trigger as well.
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as connection:
            connection.execute(text("UPDATE audit_events SET summary = 'x' WHERE event_id = :id"), {"id": event_id})
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM audit_events WHERE event_id = :id"), {"id": event_id})


def test_verification_detects_tampering_that_bypasses_the_guards():
    from app.services.audit_service import verify_chain

    with TestClient(app) as client:
        upload(client, "y.xlsx", {"S": [{"A": 1}]})
        upload(client, "z.xlsx", {"S": [{"A": 1}]})
        session_id = client.get("/api/session").json()["session_id"]

    with engine.begin() as connection:  # Simulate a DBA disabling the trigger.
        connection.execute(text("DROP TRIGGER audit_events_no_update"))
        connection.execute(text("UPDATE audit_events SET summary = 'edited' WHERE scope_id = :s AND action = 'file.uploaded'"), {"s": session_id})
        connection.execute(text(
            "CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events "
            "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
        ))
    with SessionLocal() as db:
        result = verify_chain(db, session_id)
    assert result["valid"] is False and "modified" in result["reason"]
