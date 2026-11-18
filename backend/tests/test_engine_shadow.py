from __future__ import annotations

import json
import io
from copy import deepcopy
from datetime import datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.database.session import SessionLocal
from app.models.audit import AuditEvent
from app.models.engine_shadow import EngineShadowComparison
from app.models.job import ReconciliationJob
from app.services.engine_shadow_service import (
    EngineShadowTracker,
    record_shadow_audit,
    shadow_decision,
)
from app.services.job_service import delete_job
from app.storage import get_storage


def _job() -> ReconciliationJob:
    return ReconciliationJob(
        session_id="00000000-0000-4000-8000-000000000123",
        job_type="generic",
        status="processing",
        progress=10,
        orientation="vertical",
        settings_json="{}",
    )


def _universal_data(*, matched: int = 1) -> dict:
    return {
        "metadata": {
            "engine": "rows",
            "engine_version": "rows-v1",
            "generated_at": "2026-10-22T00:00:00Z",
        },
        "statistics": {"matched_records": matched},
        "matched_records": [{"ROW (FILE 1)": 2, "ROW (FILE 2)": 3, "Primary Key": "INV-1"}],
        "exceptions": [],
        "missing_in_file_1": [],
        "missing_in_file_2": [],
    }


def test_shadow_rollout_must_be_bounded_and_keep_rows_primary():
    with pytest.raises(ValueError, match="ENGINE_SHADOW_MAX_JOBS or ENGINE_SHADOW_UNTIL"):
        Settings(_env_file=None, environment="test", engine_shadow_enabled=True)
    with pytest.raises(ValueError, match="ENGINE_SHADOW_STARTED_AT"):
        Settings(
            _env_file=None,
            environment="test",
            engine="rows",
            engine_shadow_enabled=True,
            engine_shadow_max_jobs=1,
        )
    with pytest.raises(ValueError, match="ENGINE must remain 'rows'"):
        Settings(
            _env_file=None,
            environment="test",
            engine="columns",
            engine_shadow_enabled=True,
            engine_shadow_max_jobs=1,
        )

    settings = Settings(
        _env_file=None,
        environment="test",
        engine="rows",
        engine_shadow_enabled=True,
        engine_shadow_max_jobs=1,
        engine_shadow_started_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    assert settings.engine_shadow_enabled is True


def test_shadow_tracker_persists_every_difference_audits_and_cleans_up(tmp_path):
    storage = get_storage()
    with SessionLocal() as db:
        job = _job()
        db.add(job)
        db.commit()
        db.refresh(job)

        tracker = EngineShadowTracker.create(job, tmp_path)
        primary = {"universal_data": _universal_data(matched=1)}
        candidate = {"universal_data": deepcopy(primary["universal_data"])}
        candidate["universal_data"]["metadata"].update(engine="columns", engine_version="columns-v1")
        candidate["universal_data"]["statistics"]["matched_records"] = 2

        tracker.compare_rule(
            file_pair_id="pair-1",
            sheet_rule_id="rule-1",
            report_label="Source <-> Destination",
            primary_result=primary,
            primary_seconds=0.01,
            run_shadow=lambda: candidate,
        )
        comparison = tracker.finalize(db, storage)
        db.commit()
        db.refresh(comparison)

        assert comparison.status == "different"
        assert comparison.difference_count == 1
        assert comparison.details_storage_path
        detail_path = storage.resolve_path(comparison.details_storage_path)
        details = [json.loads(line) for line in detail_path.read_text(encoding="utf-8").splitlines()]
        assert details == [{
            "schema_version": 1,
            "file_pair_id": "pair-1",
            "sheet_rule_id": "rule-1",
            "report_label": "Source <-> Destination",
            "difference": {
                "path": "statistics.matched_records",
                "kind": "value_mismatch",
                "expected": 1,
                "actual": 2,
            },
        }]

        record_shadow_audit(comparison)
        audit_event = db.query(AuditEvent).filter(AuditEvent.action == "engine.shadow.completed").one()
        assert json.loads(audit_event.after_json)["difference_count"] == 1

        settings = get_settings()
        original_enabled = settings.engine_shadow_enabled
        original_limit = settings.engine_shadow_max_jobs
        original_started_at = settings.engine_shadow_started_at
        try:
            settings.engine_shadow_enabled = True
            settings.engine_shadow_max_jobs = 1
            settings.engine_shadow_started_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
            assert shadow_decision(db, settings, job).reason == "job_limit_reached"
        finally:
            settings.engine_shadow_enabled = original_enabled
            settings.engine_shadow_max_jobs = original_limit
            settings.engine_shadow_started_at = original_started_at

        assert delete_job(db, job.id, job.session_id, storage) is True
        assert not detail_path.exists()
        assert db.query(EngineShadowComparison).filter(EngineShadowComparison.id == comparison.id).first() is None


def _workbook_bytes(rows: list[dict]) -> io.BytesIO:
    stream = io.BytesIO()
    with pd.ExcelWriter(stream, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="Sheet1", index=False)
    stream.seek(0)
    return stream


def _upload(client: TestClient, name: str, rows: list[dict]) -> str:
    response = client.post(
        "/api/files/upload",
        files={"file": (name, _workbook_bytes(rows), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_enabled_shadow_keeps_rows_user_facing_and_runs_columns_privately(client: TestClient, monkeypatch):
    from app.services import reconciliation_service

    settings = get_settings()
    monkeypatch.setattr(settings, "engine", "rows")
    monkeypatch.setattr(settings, "engine_shadow_enabled", True)
    monkeypatch.setattr(settings, "engine_shadow_max_jobs", 1)
    monkeypatch.setattr(settings, "engine_shadow_started_at", datetime(2020, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(settings, "engine_shadow_until", None)

    calls: list[str] = []
    real_run = reconciliation_service.run_generic_reconciliation

    def tracked_run(**kwargs):
        calls.append(kwargs["engine_mode"])
        return real_run(**kwargs)

    monkeypatch.setattr(reconciliation_service, "run_generic_reconciliation", tracked_run)
    source = _upload(client, "source.xlsx", [{"Invoice": "INV-1", "Amount": 100}])
    destination = _upload(client, "destination.xlsx", [{"Invoice": "INV1", "Amount": 100}])
    response = client.post("/api/reconciliation/generic", json={
        "orientation": "vertical",
        "file_pairs": [{
            "file_pair_id": "pair-1",
            "source_files": [{"file_id": source, "sheet_id": "Sheet1"}],
            "destination_files": [{"file_id": destination, "sheet_id": "Sheet1"}],
            "sheet_rules": [{
                "sheet_rule_id": "rule-1",
                "source_sheets": ["Sheet1"],
                "destination_sheets": ["Sheet1"],
                "matching_strategy": {
                    "primary_key_source": ["Invoice"],
                    "primary_key_destination": ["Invoice"],
                },
                "reconciliation_mapping": [{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}],
            }],
        }],
    })
    assert response.status_code == 200, response.text
    completed = client.get(f"/api/jobs/{response.json()['id']}")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert calls == ["rows", "columns"]

    with SessionLocal() as db:
        comparison = db.query(EngineShadowComparison).one()
        assert comparison.primary_engine == "rows"
        assert comparison.shadow_engine == "columns"
        assert comparison.status == "matched"
        assert comparison.difference_count == 0
        assert comparison.details_storage_path is None
