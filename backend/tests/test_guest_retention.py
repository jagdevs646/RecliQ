from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.database.session import SessionLocal
from app.models.account import ReportAccessToken, ReportEmailDelivery, WorkspaceQuota
from app.models.alias import EntityAlias
from app.models.file import UploadedFile
from app.models.job import ReconciliationJob
from app.models.report import Report
from app.models.review import ReviewOutcome
from app.models.template import ReconciliationTemplate, ReconciliationTemplateVersion
from app.services.guest_retention import purge_expired_guest_data
from app.storage import get_storage

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
OLD = NOW - timedelta(hours=25)
FRESH = NOW - timedelta(hours=2)


def _stored(tmp_path, path: str) -> str:
    source = tmp_path / uuid.uuid4().hex
    source.write_bytes(b"stored-bytes")
    get_storage().save_file(source, path)
    return path


def _upload(db, tmp_path, scope: str, created_at: datetime) -> UploadedFile:
    name = f"{uuid.uuid4()}.xlsx"
    record = UploadedFile(
        session_id=scope, original_filename="books.xlsx", stored_filename=name, storage_backend="local",
        storage_path=_stored(tmp_path, f"uploads/{scope}/{name}"), size_bytes=12, created_at=created_at,
    )
    db.add(record)
    db.flush()
    return record


def _run(db, scope: str, created_at: datetime, *files: UploadedFile, status: str = "completed") -> ReconciliationJob:
    job = ReconciliationJob(
        session_id=scope, job_type="generic", status=status, created_at=created_at,
        input_file_1_id=files[0].id if files else None, input_file_2_id=files[1].id if len(files) > 1 else None,
    )
    db.add(job)
    db.flush()
    return job


def _report(db, tmp_path, job: ReconciliationJob, created_at: datetime) -> Report:
    report = Report(
        session_id=job.session_id, filename="report.xlsx", storage_backend="local", size_bytes=12,
        storage_path=_stored(tmp_path, f"reports/{job.session_id}/{uuid.uuid4()}-report.xlsx"), created_at=created_at,
    )
    db.add(report)
    db.flush()
    job.report_id = report.id
    return report


def _alias(db, scope: str, created_at: datetime) -> None:
    db.add(EntityAlias(
        scope_id=scope, canonical="IBM", variant="International Business Machines", canonical_key="ibm",
        variant_key="internationalbusinessmachines", created_by="test", created_at=created_at,
    ))


def test_expired_guest_data_is_deleted_and_accounts_are_kept(client, tmp_path):
    guest, empty_guest, account = str(uuid.uuid4()), str(uuid.uuid4()), f"user-{uuid.uuid4()}"
    db = SessionLocal()
    try:
        # A guest run from yesterday, with its report, a review decision and an emailed link.
        old_source, old_destination = _upload(db, tmp_path, guest, OLD), _upload(db, tmp_path, guest, OLD)
        old_run = _run(db, guest, OLD, old_source, old_destination)
        old_report = _report(db, tmp_path, old_run, OLD)
        db.add(ReviewOutcome(job_id=old_run.id, pair_key="A|B", outcome="confirmed"))
        delivery = ReportEmailDelivery(
            scope_id=guest, job_id=old_run.id, report_id=old_report.id, recipients_json='["a@example.com"]',
            subject="Report", message="", idempotency_key="guest-retention-0001", delivery_method="secure_link",
            include_summary=True, status="sent", attempt_count=1, transport="local_log",
        )
        db.add(delivery)
        db.flush()
        db.add(ReportAccessToken(delivery_id=delivery.id, token_hash="0" * 64, recipient_email="a@example.com", expires_at=NOW))
        # Yesterday's upload that today's run still uses is kept with that run.
        reused = _upload(db, tmp_path, guest, OLD)
        today_run = _run(db, guest, FRESH, reused, _upload(db, tmp_path, guest, FRESH))
        # Never-run upload, a still-running old run, saved setup and learning.
        never_run = _upload(db, tmp_path, guest, OLD)
        running = _run(db, guest, OLD, status="processing")
        template = ReconciliationTemplate(session_id=guest, name="Bank", created_at=OLD)
        db.add(template)
        db.flush()
        db.add(ReconciliationTemplateVersion(template_id=template.id, version=1, config_json="{}", created_by="test"))
        _alias(db, guest, OLD)
        _alias(db, guest, FRESH)
        # A second guest with only old data, and an account with old data.
        _run(db, empty_guest, OLD, _upload(db, tmp_path, empty_guest, OLD))
        account_file = _upload(db, tmp_path, account, OLD)
        account_run = _run(db, account, OLD, account_file)
        _alias(db, account, OLD)
        for scope in (guest, empty_guest, account):
            db.add(WorkspaceQuota(scope_id=scope, storage_bytes=0, day_key="2026-10-11", month_key="2026-10"))
        db.commit()
        ids = {
            "old_run": old_run.id, "old_report": old_report.id, "old_files": {old_source.id, old_destination.id},
            "never_run": never_run.id, "reused": reused.id, "today_run": today_run.id, "running": running.id,
            "account_run": account_run.id, "account_file": account_file.id,
            "old_paths": [old_source.storage_path, old_report.storage_path, never_run.storage_path],
        }
    finally:
        db.close()

    removed = purge_expired_guest_data(now=NOW)

    assert removed["runs"] == 2  # the guest's finished run and the other guest's run
    assert removed["files"] == 1  # the never-run upload; the others went with their runs
    assert removed["saved_setups"] == 1
    assert removed["aliases"] == 1
    assert removed["workspaces"] == 1  # only the guest left with nothing
    db = SessionLocal()
    try:
        assert db.get(ReconciliationJob, ids["old_run"]) is None
        assert db.get(Report, ids["old_report"]) is None
        assert not db.query(UploadedFile).filter(UploadedFile.id.in_(ids["old_files"] | {ids["never_run"]})).count()
        assert not db.query(ReviewOutcome).filter_by(job_id=ids["old_run"]).count()
        assert not db.query(ReportEmailDelivery).filter_by(job_id=ids["old_run"]).count()
        assert not db.query(ReportAccessToken).count()
        assert not db.query(ReconciliationTemplate).filter_by(session_id=guest).count()
        assert not db.query(ReconciliationTemplateVersion).count()
        assert db.query(EntityAlias).filter_by(scope_id=guest).count() == 1  # today's alias stays
        # Still in use or still running: kept.
        assert db.get(ReconciliationJob, ids["today_run"]) is not None
        assert db.get(UploadedFile, ids["reused"]) is not None
        assert db.get(ReconciliationJob, ids["running"]) is not None
        # Accounts are never touched.
        assert db.get(ReconciliationJob, ids["account_run"]) is not None
        assert db.get(UploadedFile, ids["account_file"]) is not None
        assert db.query(EntityAlias).filter_by(scope_id=account).count() == 1
        assert {quota.scope_id for quota in db.query(WorkspaceQuota)} == {guest, account}
    finally:
        db.close()
    storage = get_storage()
    for path in ids["old_paths"]:
        assert not (storage.base_path / path).exists()

    assert not any(purge_expired_guest_data(now=NOW).values())  # nothing left to remove
