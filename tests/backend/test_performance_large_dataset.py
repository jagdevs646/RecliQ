import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

import pandas as pd
import pytest

from app.reconciliation_engine.engine import run_generic_reconciliation, run_gst_reconciliation
from app.reconciliation_engine.progress_tracker import ProgressTracker


def test_large_generic_reconciliation_performance(tmp_path: Path):
    file1 = tmp_path / "large_source.xlsx"
    file2 = tmp_path / "large_dest.xlsx"
    output = tmp_path / "large_report.xlsx"

    n_rows = 10000
    rows1 = [
        {"Invoice No": f"INV-{i:05d}", "Amount": 100 + i, "Vendor": f"Vendor {i % 100}"}
        for i in range(n_rows)
    ]
    rows2 = [
        {"Invoice_Number": f"INV{i:05d}", "Invoice Amount": 100 + i, "Supplier": f"Vendor {i % 100}"}
        for i in range(n_rows)
    ]

    pd.DataFrame(rows1).to_excel(file1, index=False)
    pd.DataFrame(rows2).to_excel(file2, index=False)

    progress_events = []

    def on_progress(percentage: int, step: str):
        progress_events.append((percentage, step))

    start_time = time.time()
    summary = run_generic_reconciliation(
        file1,
        file2,
        output,
        key_file_1="Invoice No",
        key_file_2="Invoice_Number",
        rules=[
            {"file_1_fields": ["Amount"], "file_2_fields": ["Invoice Amount"]},
            {"file_1_fields": ["Vendor"], "file_2_fields": ["Supplier"]},
        ],
        progress_callback=on_progress,
    )
    elapsed = time.time() - start_time

    assert output.exists()
    assert summary["matched_records"] == n_rows
    assert summary["only_in_file_1"] == 0
    assert summary["only_in_file_2"] == 0
    assert elapsed < 15.0  # Must process 10,000 rows rapidly
    assert len(progress_events) >= 5


def test_large_gst_reconciliation_performance(tmp_path: Path):
    file1 = tmp_path / "gst_large_1.xlsx"
    file2 = tmp_path / "gst_large_2.xlsx"
    output = tmp_path / "gst_large_report.xlsx"

    n_rows = 5000
    rows1 = [
        {
            "GSTR": f"27ABCDE{i % 10:04d}F1Z5",
            "NAME OF TRADER/FIRM/COMPANY": f"COMPANY {i % 50}",
            "INVOICE NO.": f"INV-{i:05d}",
            "INVOICE DATE": "2026-07-01",
            "TAXABLE VALUE": 1000,
            "IGST": 180,
            "CGST": 0,
            "SGST": 0,
            "CESS": 0,
            "INVOICE VALUE": 1180,
        }
        for i in range(n_rows)
    ]
    rows2 = [
        {
            "GSTR": f"27ABCDE{i % 10:04d}F1Z5",
            "NAME OF TRADER/FIRM/COMPANY": f"COMPANY {i % 50}",
            "INVOICE NO.": f"INV{i:05d}",
            "INVOICE DATE": "2026-07-01",
            "TAXABLE VALUE": 1000,
            "IGST": 180,
            "CGST": 0,
            "SGST": 0,
            "CESS": 0,
            "INVOICE VALUE": 1180,
        }
        for i in range(n_rows)
    ]

    pd.DataFrame(rows1).to_excel(file1, index=False)
    pd.DataFrame(rows2).to_excel(file2, index=False)

    start_time = time.time()
    summary = run_gst_reconciliation(file1, file2, output)
    elapsed = time.time() - start_time

    assert output.exists()
    assert summary["matched_records"] == n_rows
    assert summary["only_in_file_1"] == 0
    assert summary["only_in_file_2"] == 0
    assert elapsed < 15.0
