import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

import pandas as pd
from fastapi.testclient import TestClient

from _helpers import upload, wait_for_job
from app.main import app
from app.reconciliation.gst import REQUIRED_COLUMNS, run_gst_reconciliation
from app.reconciliation_engine.engine import run_gst_reconciliation as run_gst_engine


def gst_row(invoice_number: str) -> dict[str, object]:
    return {
        "GSTR": "27ABCDE1234F1Z5",
        "NAME OF TRADER/FIRM/COMPANY": "ABC PRIVATE LIMITED",
        "INVOICE NO.": invoice_number,
        "INVOICE DATE": "2026-07-01",
        "TAXABLE VALUE": 1000,
        "IGST": 180,
        "CGST": 0,
        "SGST": 0,
        "CESS": 0,
        "INVOICE VALUE": 1180,
    }


def test_gst_reconciliation_returns_matched_summary_without_scope_error(tmp_path: Path):
    file1 = tmp_path / "gst-source.xlsx"
    file2 = tmp_path / "gst-destination.xlsx"
    output = tmp_path / "gst-report.xlsx"

    pd.DataFrame([gst_row("INV001")], columns=REQUIRED_COLUMNS).to_excel(file1, index=False)
    pd.DataFrame([gst_row("INV001")], columns=REQUIRED_COLUMNS).to_excel(file2, index=False)

    summary = run_gst_reconciliation(file1, file2, output)

    assert output.exists()
    assert summary["matched_records"] == 1
    assert summary["fully_matched_records"] == 1
    assert summary["report_rows"] == 0


def test_gst_reconciliation_handles_timestamps_and_dates(tmp_path: Path):
    file1 = tmp_path / "gst-source-dates.xlsx"
    file2 = tmp_path / "gst-dest-dates.xlsx"
    output = tmp_path / "gst-report-dates.xlsx"

    row1 = gst_row("INV001")
    row1["INVOICE DATE"] = pd.Timestamp("2026-07-01 10:30:00")
    row2 = gst_row("INV002")
    row2["INVOICE DATE"] = pd.Timestamp("2026-07-02")
    row2["TAXABLE VALUE"] = 2000

    # Destination has one matching, one difference, one missing
    row_dest1 = gst_row("INV001")
    row_dest1["INVOICE DATE"] = pd.Timestamp("2026-07-01 10:30:00")
    row_dest2 = gst_row("INV002")
    row_dest2["INVOICE DATE"] = pd.Timestamp("2026-07-02")
    row_dest2["TAXABLE VALUE"] = 2500 # Mismatch

    row_dest3 = gst_row("INV003")
    row_dest3["INVOICE DATE"] = pd.Timestamp("2026-07-03")

    pd.DataFrame([row1, row2], columns=REQUIRED_COLUMNS).to_excel(file1, index=False)
    pd.DataFrame([row_dest1, row_dest2, row_dest3], columns=REQUIRED_COLUMNS).to_excel(file2, index=False)

    summary = run_gst_reconciliation(file1, file2, output)

    assert output.exists()
    assert summary["matched_records"] == 2
    assert summary["only_in_file_2"] == 1

    # Check that raw data JSON exists and is valid JSON
    raw_path = output.with_name(f"{output.stem}_data.json")
    assert raw_path.exists()
    import json
    with open(raw_path, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert "exceptions" in loaded


def test_gst_differences_are_listed_not_only_counted():
    books, portal = gst_row("INV001"), gst_row("INV001")
    portal.update({"IGST": 185, "INVOICE VALUE": 1185, "INVOICE DATE": "2026-07-05",
                   "NAME OF TRADER/FIRM/COMPANY": "XYZ ENTERPRISES"})
    result = run_gst_engine(pd.DataFrame([books]), pd.DataFrame([portal]), Path("unused.xlsx"), write_report=False)

    assert result["summary"]["report_rows"] == 1
    listed = {line["Field"]: line for line in result["universal_data"]["exceptions"]}
    assert set(listed) == {"IGST", "INVOICE VALUE", "INVOICE DATE", "NAME OF TRADER/FIRM/COMPANY"}
    assert listed["IGST"]["Primary Key"] == "27ABCDE1234F1Z5 | INV001"
    assert (listed["IGST"]["File 1 Value"], listed["IGST"]["File 2 Value"], listed["IGST"]["Difference"]) == (180.0, 185.0, -5.0)
    assert listed["NAME OF TRADER/FIRM/COMPANY"]["File 2 Value"] == "XYZ ENTERPRISES"


def test_gst_pairs_to_confirm_are_listed_for_review():
    books, portal = gst_row("INV001"), gst_row("INV001")
    books["NAME OF TRADER/FIRM/COMPANY"] = "Sharma Steel Traders"
    portal["NAME OF TRADER/FIRM/COMPANY"] = "Sharma Steels Traders"  # 97% similar: review, not a difference.
    result = run_gst_engine(pd.DataFrame([books]), pd.DataFrame([portal]), Path("unused.xlsx"), write_report=False)

    assert result["summary"]["confidence_review"] == 1 and result["summary"]["report_rows"] == 0
    assert result["universal_data"]["exceptions"] == []
    [review] = result["universal_data"]["identity_resolution"]
    assert review["IDENTITY CLASSIFICATION"] == "EXCEPTION_MATCH"
    assert review["MATCH KEY"] == review["CANDIDATE KEY"] == "27ABCDE1234F1Z5 | INV001"
    assert "97% similar" in review["MATCH EXPLANATION"]


def test_a_gst_run_reads_every_selected_sheet():
    july = [gst_row("INV001"), gst_row("INV002")]
    august = [gst_row("INV003")]
    portal = [gst_row("INV001"), gst_row("INV002"), gst_row("INV003")]
    with TestClient(app) as client:
        books_id = upload(client, "books.xlsx", {"July": july, "August": august})
        portal_id = upload(client, "portal.xlsx", {"GSTR-2B": portal})
        response = client.post("/api/reconciliation/gst", json={
            "source_files_1": [{"file_id": books_id, "sheet_id": "July"}, {"file_id": books_id, "sheet_id": "August"}],
            "source_files_2": [{"file_id": portal_id, "sheet_id": "GSTR-2B"}],
        })
        assert response.status_code in (200, 201), response.text
        job = wait_for_job(client, response.json()["id"])
        summary = client.get(f"/api/reports/job/{job['id']}/summary").json()

    assert job["status"] == "completed", job
    assert summary["source_records"] == 3  # Both sheets, not only July.
    assert summary["fully_matched_records"] == 3 and summary["only_in_file_2"] == 0

