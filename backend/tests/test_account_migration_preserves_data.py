from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.config import get_settings


def test_account_migrations_preserve_populated_guest_records(tmp_path, monkeypatch):
    database_url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    session_id = str(uuid4())
    file_id, report_id, job_id = str(uuid4()), str(uuid4()), str(uuid4())
    decision_id = str(uuid4())

    try:
        command.upgrade(config, "20260929_0001")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO uploaded_files "
                "(id, session_id, original_filename, stored_filename, storage_backend, storage_path, content_type, size_bytes, created_at, updated_at) "
                "VALUES (:id, :owner, 'legacy.xlsx', 'stored.xlsx', 'local', 'uploads/legacy/stored.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 2000000000, :now, :now)"
            ), {"id": file_id, "owner": session_id, "now": now})
            connection.execute(text(
                "INSERT INTO reports "
                "(id, session_id, filename, storage_backend, storage_path, size_bytes, summary_json, created_at, updated_at) "
                "VALUES (:id, :owner, 'legacy-report.xlsx', 'local', 'reports/legacy/report.xlsx', 3000000000, :summary, :now, :now)"
            ), {
                "id": report_id,
                "owner": session_id,
                "summary": json.dumps({"matched_records": 5, "only_in_file_1": 2, "only_in_file_2": 3, "field_discrepancies": 1, "confidence_review": 4}),
                "now": now,
            })
            connection.execute(text(
                "INSERT INTO reconciliation_jobs "
                "(id, session_id, job_type, status, progress, orientation, settings_json, input_file_1_id, report_id, attempts, created_at, updated_at) "
                "VALUES (:id, :owner, 'generic', 'completed', 100, 'vertical', '{}', :file_id, :report_id, 1, :now, :now)"
            ), {"id": job_id, "owner": session_id, "file_id": file_id, "report_id": report_id, "now": now})
            connection.execute(text(
                "INSERT INTO match_decisions "
                "(id, scope_id, job_id, column_hint, value_1, value_2, pair_key, decision, confidence, note, decided_by, created_at, updated_at) "
                "VALUES (:id, :scope, :job_id, 'Vendor', 'Acme Ltd', 'ACME LIMITED', 'vendor:acme', 'accepted', 94, '', 'guest', :now, :now)"
            ), {"id": decision_id, "scope": session_id, "job_id": job_id, "now": now})

        command.upgrade(config, "head")
        with engine.connect() as connection:
            stored_file = connection.execute(text("SELECT session_id, size_bytes, row_count_total FROM uploaded_files WHERE id=:id"), {"id": file_id}).one()
            stored_report = connection.execute(text("SELECT session_id, size_bytes, data_size_bytes FROM reports WHERE id=:id"), {"id": report_id}).one()
            stored_job = connection.execute(text("SELECT session_id, matched_records_count, exception_count, review_count FROM reconciliation_jobs WHERE id=:id"), {"id": job_id}).one()
            stored_decision = connection.execute(text("SELECT pair_key, decision FROM match_decisions WHERE id=:id"), {"id": decision_id}).one()
            outcome = connection.execute(text("SELECT outcome FROM review_outcomes WHERE job_id=:job_id AND pair_key='vendor:acme'"), {"job_id": job_id}).scalar_one()
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

        assert stored_file == (session_id, 2_000_000_000, 0)
        assert stored_report == (session_id, 3_000_000_000, 0)
        assert stored_job == (session_id, 5, 6, 3)
        assert stored_decision == ("vendor:acme", "accepted")
        assert outcome == "accepted"
        assert version == "20261012_0001"
        engine.dispose()
    finally:
        get_settings.cache_clear()
