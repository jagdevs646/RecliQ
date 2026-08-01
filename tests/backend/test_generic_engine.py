import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

import pandas as pd
import pytest

from app.reconciliation.generic import run_generic_reconciliation


def test_generic_reconciliation_creates_report(tmp_path: Path):
    file1 = tmp_path / "file1.xlsx"
    file2 = tmp_path / "file2.xlsx"
    output = tmp_path / "report.xlsx"

    pd.DataFrame(
        [
            {"Invoice No": "INV-005", "Amount": 1000, "Vendor": "ABC PVT LTD"},
            {"Invoice No": "INV-007", "Amount": 700, "Vendor": "Other Co"},
        ]
    ).to_excel(file1, index=False)
    pd.DataFrame(
        [
            {"Invoice_Number": "INV005", "Invoice Amount": 1000, "Supplier": "ABC PRIVATE LIMITED"},
            {"Invoice_Number": "INV009", "Invoice Amount": 900, "Supplier": "Missing Co"},
        ]
    ).to_excel(file2, index=False)

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
    )

    assert output.exists()
    assert summary["only_in_file_1"] == 1
    assert summary["only_in_file_2"] == 1


def test_generic_reconciliation_combines_any_number_of_numeric_fields(tmp_path: Path):
    file1 = tmp_path / "source.xlsx"
    file2 = tmp_path / "destination.xlsx"
    output = tmp_path / "report.xlsx"

    pd.DataFrame([
        {"Employee ID": "E001", "ST Hours": 10},
        {"Employee ID": "E002", "ST Hours": 5},
    ]).to_excel(file1, index=False)
    pd.DataFrame([
        {"ID": "E001", "Standard Hours": 5, "ST2 Hours": 3, "ST3 Hours": 2},
        {"ID": "E002", "Standard Hours": 3, "ST2 Hours": 2, "ST3 Hours": 0},
    ]).to_excel(file2, index=False)

    summary = run_generic_reconciliation(
        file1,
        file2,
        output,
        key_file_1="Employee ID",
        key_file_2="ID",
        rules=[{
            "file_1_fields": ["ST Hours"],
            "file_2_fields": ["Standard Hours", "ST2 Hours", "ST3 Hours"],
        }],
    )

    assert output.exists()
    assert summary["report_rows"] == 0
    assert summary["matched_records"] == 2


def test_combined_mapping_rejects_non_numeric_columns(tmp_path: Path):
    file1 = tmp_path / "source.xlsx"
    file2 = tmp_path / "destination.xlsx"
    output = tmp_path / "report.xlsx"

    pd.DataFrame([{"Employee ID": "E-001", "ST Hours": 10}]).to_excel(file1, index=False)
    pd.DataFrame([{"ID": "E-001", "Standard Hours": 10, "Comment": "not numeric"}]).to_excel(file2, index=False)

    with pytest.raises(ValueError, match="Combined column mappings require numeric columns.*File 2: COMMENT"):
        run_generic_reconciliation(
            file1,
            file2,
            output,
            key_file_1="Employee ID",
            key_file_2="ID",
            rules=[{
                "file_1_fields": ["ST Hours"],
                "file_2_fields": ["Standard Hours", "Comment"],
            }],
        )
