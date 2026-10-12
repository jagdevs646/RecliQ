import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

from app.reconciliation_engine.universal_reporter import generate_enterprise_report
from app.services.report_data_store import ParquetRecordRows, query_report_category, read_report_tables, write_report_tables


def _universal_data() -> dict:
    pair_metadata = {
        "metadata": {
            "reconciliation_name": "Generic Reconciliation",
            "file_1_name": "Books.xlsx",
            "file_2_name": "Bank.xlsx",
            "matching_keys": ["Invoice"],
            "compared_fields": ["Amount"],
        },
        "statistics": {
            "total_file_1": 3,
            "total_file_2": 3,
            "matched": 2,
            "mismatched": 1,
            "missing_in_file_1": 0,
            "missing_in_file_2": 0,
            "identity": {},
        },
        "overall_status": "EXCEPTIONS FOUND",
        "control_checks": [],
        "sheet_rules": [],
    }
    return {
        **pair_metadata,
        "exceptions": [
            {
                "Primary Key": "Needle-1", "Field": "Amount", "File 1 Value": 10, "File 2 Value": 12,
                "Difference": 2, "File Pair ID": "north", "Sheet Rule ID": "north-rule",
            },
            {
                "Primary Key": "Other-1", "Field": "Amount", "File 1 Value": 4, "File 2 Value": 5,
                "Difference": 1, "File Pair ID": "south", "Sheet Rule ID": "south-rule",
            },
        ],
        "matched_records": [
            {"MATCH KEY": "N-1", "MATCHED KEY": "N-1", "Amount": 2, "ROW (FILE 1)": 2, "ROW (FILE 2)": 2,
             "File Pair ID": "north", "Sheet Rule ID": "north-rule"},
            {"MATCH KEY": "N-2", "MATCHED KEY": "N-2", "Amount": 100, "ROW (FILE 1)": 3, "ROW (FILE 2)": 3,
             "File Pair ID": "north", "Sheet Rule ID": "north-rule"},
            {"MATCH KEY": "S-1", "MATCHED KEY": "S-1", "Amount": 8, "ROW (FILE 1)": 2, "ROW (FILE 2)": 2,
             "File Pair ID": "south", "Sheet Rule ID": "south-rule"},
        ],
        "missing_in_file_1": [],
        "missing_in_file_2": [],
        "field_differences": [],
        "identity_resolution": [],
        "auto_resolved": [],
        "file_pair_reports": {
            "north": pair_metadata,
            "south": pair_metadata,
        },
    }


def test_parquet_results_page_by_scope_and_rebuild_report_without_materializing(tmp_path: Path):
    source = _universal_data()
    data_dir = tmp_path / "result-data"
    write_report_tables(source, data_dir)

    loaded = read_report_tables(data_dir)
    assert isinstance(loaded["exceptions"], ParquetRecordRows)
    assert len(loaded["matched_records"]) == 3

    page = query_report_category(
        data_dir,
        "matched_records",
        file_pair_id="north",
        sort_by="Amount",
        sort_direction="desc",
        limit=1,
    )
    assert page["total_rows"] == 2
    assert page["rows"][0]["MATCH KEY"] == "N-2"
    searched = query_report_category(data_dir, "exceptions", search="needle", limit=5)
    assert searched["total_rows"] == 1
    assert searched["rows"][0]["Primary Key"] == "Needle-1"

    scoped = read_report_tables(data_dir, file_pair_id="north")
    assert len(scoped["matched_records"]) == 2
    assert len(scoped["exceptions"]) == 1
    assert list(scoped["exceptions"])[0]["Primary Key"] == "Needle-1"

    output = tmp_path / "rebuilt.xlsx"
    generated = generate_enterprise_report(scoped, {}, output)
    assert output.is_file()
    assert generated.sheet_counts["06 Matched"] == 2


def test_legacy_json_report_preview_custom_download_and_delete():
    import json

    from fastapi.testclient import TestClient

    from _helpers import single_rule_plan, upload, wait_for_job
    from app.database.session import SessionLocal
    from app.main import app
    from app.models.job import ReconciliationJob
    from app.models.report import Report
    from app.services.report_data_store import REPORT_DATA_CATEGORIES
    from app.storage import get_storage
    from app.storage.base import report_data_category_path, report_data_manifest_path
    from app.utils.json_encoder import safe_json_dump

    keyed = {
        "matching_strategy": {"primary_key_source": ["Invoice"], "primary_key_destination": ["Invoice"]},
        "reconciliation_mapping": [{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}],
    }
    with TestClient(app) as client:
        source = upload(client, "books.xlsx", {"Books": [{"Invoice": "A", "Amount": 10}]})
        destination = upload(client, "bank.xlsx", {"Bank": [{"Invoice": "A", "Amount": 12}]})
        plan = single_rule_plan(source, destination, "Books", "Bank", **keyed)
        job = wait_for_job(client, client.post("/api/reconciliation/generic", json=plan).json()["id"])

        with SessionLocal() as db:
            job_record = db.query(ReconciliationJob).filter(ReconciliationJob.id == job["id"]).one()
            report = db.query(Report).filter(Report.id == job_record.report_id).one()
            report_storage_path = report.storage_path

        storage = get_storage()
        report_path = storage.resolve_path(report_storage_path)
        data_directory = storage.resolve_path(report_data_manifest_path(report_storage_path)).parent
        legacy_path = report_path.with_name(f"{report_path.stem}_data.json")
        legacy_data = read_report_tables(data_directory)
        for category in REPORT_DATA_CATEGORIES:
            legacy_data[category] = list(legacy_data[category])
        with legacy_path.open("w", encoding="utf-8") as handle:
            safe_json_dump(legacy_data, handle, ensure_ascii=False)
        for category in REPORT_DATA_CATEGORIES:
            storage.delete_file(report_data_category_path(report_storage_path, category))
        storage.delete_file(report_data_manifest_path(report_storage_path))

        preview = client.get(f"/api/reports/job/{job['id']}/preview", params={"category": "discrepancies"})
        assert preview.status_code == 200, preview.text
        assert preview.json()["total_rows"] == 1
        custom = client.post(f"/api/reports/job/{job['id']}/download_custom", json={})
        assert custom.status_code == 200, custom.text
        assert custom.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument")

        assert client.delete(f"/api/jobs/{job['id']}").status_code == 204
        assert not legacy_path.exists()
        assert not report_path.exists()
        assert not data_directory.exists()
