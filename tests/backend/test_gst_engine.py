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
