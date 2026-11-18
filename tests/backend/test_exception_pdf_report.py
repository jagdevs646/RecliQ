import io
import sys
from pathlib import Path

import pdfplumber
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

from _helpers import single_rule_plan, upload, wait_for_job
from app.main import app
from app.reconciliation_engine.exception_pdf_reporter import generate_exception_pdf_report


def _pdf_text(payload: bytes | Path) -> str:
    source = io.BytesIO(payload) if isinstance(payload, bytes) else str(payload)
    with pdfplumber.open(source) as document:
        return "\n".join(page.extract_text() or "" for page in document.pages)


def test_exception_pdf_contains_the_three_review_tables(tmp_path: Path):
    output = tmp_path / "exceptions.pdf"
    generated = generate_exception_pdf_report(
        {
            "metadata": {
                "file_1_name": "Books.xlsx",
                "file_2_name": "Bank.xlsx",
                "matching_keys": ["Invoice"],
            },
            "sheet_rules": [{
                "primary_key_source": ["Invoice"],
                "primary_key_destination": ["Document number"],
            }],
            "exceptions": [{
                "Primary Key": "INV-100",
                "Sheet Pair": "Sheet1 -> Details",
                "Field": "Amount",
                "File 1 Value": 100,
                "File 2 Value": 125,
                "Difference %": -0.2,
            }],
            "missing_in_file_2": [{
                "Invoice": "ONLY-BOOKS",
                "Customer": "Acme Ltd",
                "Amount": 55,
                "Region": "North",
                "Cost Center": "CC-01",
                "Private note": "DO-NOT-SHOW-BOOKS",
                "Sheet Pair": "Sheet1 -> Details",
                "ROW (FILE 1)": 3,
            }],
            "missing_in_file_1": [{
                "Document Number": "ONLY-BANK",
                "Counterparty": "Globex Ltd",
                "Amount": 80,
                "Region": "South",
                "Cost Center": "CC-02",
                "Private note": "DO-NOT-SHOW-BANK",
                "Sheet Pair": "Sheet1 -> Details",
                "ROW (FILE 2)": 4,
            }],
        },
        output,
    )

    assert output.read_bytes().startswith(b"%PDF")
    assert generated.section_totals == {"exceptions": 1, "only_in_file_1": 1, "only_in_file_2": 1}
    text = _pdf_text(output)
    for expected in (
        "Exception review report",
        "Field exceptions",
        "Only in Books.xlsx",
        "Only in Bank.xlsx",
        "INV-100",
        "ONLY-BOOKS",
        "ONLY-BANK",
        "Books.xlsx value",
        "Bank.xlsx value",
        "Difference %",
        "-20.00%",
        "Customer",
        "Counterparty",
    ):
        assert expected in text
    for unexpected in ("Sheet1 -> Details", "Private note", "DO-NOT-SHOW-BOOKS", "DO-NOT-SHOW-BANK"):
        assert unexpected not in text


def test_exception_pdf_download_is_session_scoped_and_uses_saved_results():
    with TestClient(app) as client:
        source = upload(client, "books.xlsx", {"Books": [
            {"Invoice": "INV-1", "Amount": 100, "Description": "Difference"},
            {"Invoice": "ONLY-BOOKS", "Amount": 55, "Description": "Source only"},
        ]})
        destination = upload(client, "bank.xlsx", {"Bank": [
            {"Invoice": "INV-1", "Amount": 125, "Description": "Difference"},
            {"Invoice": "ONLY-BANK", "Amount": 80, "Description": "Destination only"},
        ]})
        plan = single_rule_plan(
            source,
            destination,
            "Books",
            "Bank",
            matching_strategy={"primary_key_source": ["Invoice"], "primary_key_destination": ["Invoice"]},
            reconciliation_mapping=[{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}],
        )
        job = wait_for_job(client, client.post("/api/reconciliation/generic", json=plan).json()["id"])
        assert job["status"] == "completed", job.get("error_message")

        response = client.get(f"/api/reports/job/{job['id']}/download_exception_pdf")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/pdf")
        assert response.content.startswith(b"%PDF")
        text = _pdf_text(response.content)
        for expected in ("INV-1", "ONLY-BOOKS", "ONLY-BANK", "AMOUNT"):
            assert expected in text

        with TestClient(app) as other_client:
            other_response = other_client.get(f"/api/reports/job/{job['id']}/download_exception_pdf")
            assert other_response.status_code == 404


def test_exception_pdf_requires_a_selected_file_pair_for_multi_pair_jobs():
    with TestClient(app) as client:
        source = upload(client, "source.xlsx", {
            "North": [{"Invoice": "N-1", "Amount": 10}],
            "South": [{"Invoice": "S-1", "Amount": 20}],
        })
        destination = upload(client, "destination.xlsx", {
            "North": [{"Invoice": "N-1", "Amount": 12}],
            "South": [{"Invoice": "S-1", "Amount": 22}],
        })
        payload = {
            "orientation": "vertical",
            "file_pairs": [
                {
                    "file_pair_id": pair_id,
                    "source_files": [{"file_id": source, "sheet_id": sheet}],
                    "destination_files": [{"file_id": destination, "sheet_id": sheet}],
                    "sheet_rules": [{
                        "sheet_rule_id": f"{pair_id}-rule",
                        "source_sheets": [sheet],
                        "destination_sheets": [sheet],
                        "matching_strategy": {"primary_key_source": ["Invoice"], "primary_key_destination": ["Invoice"]},
                        "reconciliation_mapping": [{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}],
                        "report_label": sheet,
                    }],
                }
                for pair_id, sheet in (("north", "North"), ("south", "South"))
            ],
        }
        job = wait_for_job(client, client.post("/api/reconciliation/generic", json=payload).json()["id"])
        assert job["status"] == "completed", job.get("error_message")

        needs_scope = client.get(f"/api/reports/job/{job['id']}/download_exception_pdf")
        assert needs_scope.status_code == 422

        north = client.get(
            f"/api/reports/job/{job['id']}/download_exception_pdf",
            params={"file_pair_id": "north"},
        )
        assert north.status_code == 200, north.text
        north_text = _pdf_text(north.content)
        assert "N-1" in north_text
        assert "S-1" not in north_text
