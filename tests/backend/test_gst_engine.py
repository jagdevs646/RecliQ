import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

import pandas as pd

from app.reconciliation.gst import REQUIRED_COLUMNS, run_gst_reconciliation


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

