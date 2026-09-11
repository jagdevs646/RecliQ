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


def test_generic_reconciliation_uses_all_composite_key_columns(tmp_path: Path):
    file1 = tmp_path / "source.xlsx"
    file2 = tmp_path / "destination.xlsx"
    output = tmp_path / "report.xlsx"

    pd.DataFrame(
        [
            {"Invoice": "INV-100", "Entity": "North", "Amount": 100},
            {"Invoice": "INV-100", "Entity": "South", "Amount": 200},
        ]
    ).to_excel(file1, index=False)
    pd.DataFrame(
        [{"Invoice Number": "INV100", "Company": "South", "Amount": 200}]
    ).to_excel(file2, index=False)

    result = run_generic_reconciliation(
        file1,
        file2,
        output,
        key_file_1=["Invoice", "Entity"],
        key_file_2=["Invoice Number", "Company"],
        rules=[{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}],
    )

    assert result["matched_records"] == 1
    assert result["only_in_file_1"] == 1
    assert result["universal_data"]["matched_records"][0]["COMPOSITE MATCH KEY"] == "INV-100 | South"
    assert result["universal_data"]["missing_in_file_2"][0]["ENTITY"] == "North"


def _secondary_conditions():
    return [
        {
            "source_column": "Transaction Date",
            "destination_column": "Posting Date",
            "comparison_method": "normalized_date",
        },
        {
            "source_column": "Amount",
            "destination_column": "Gross Amount",
            "comparison_method": "numeric_tolerance",
            "numeric_tolerance": 0,
        },
    ]


def _secondary_match_result(tmp_path: Path, destination_rows: list[dict]):
    file1 = tmp_path / "source.xlsx"
    file2 = tmp_path / "destination.xlsx"
    output = tmp_path / "report.xlsx"
    pd.DataFrame(
        [{"Customer": "Alpah Consulting", "Transaction Date": "7 September 2026", "Amount": 100}]
    ).to_excel(file1, index=False)
    pd.DataFrame(destination_rows).to_excel(file2, index=False)
    return run_generic_reconciliation(
        file1,
        file2,
        output,
        key_file_1="Customer",
        key_file_2="Customer Name",
        rules=[{"file_1_fields": ["Amount"], "file_2_fields": ["Gross Amount"]}],
        secondary_conditions=_secondary_conditions(),
    )


def test_secondary_conditions_create_exception_identity_match(tmp_path: Path):
    result = _secondary_match_result(
        tmp_path,
        [{"Customer Name": "Alpha Consulting", "Posting Date": "2026-09-07", "Gross Amount": 100}],
    )

    assert result["exception_matches"] == 1
    assert result["matched_records"] == 1
    audit = result["universal_data"]["identity_resolution"][0]
    assert audit["IDENTITY CLASSIFICATION"] == "EXCEPTION_MATCH"
    assert "Primary key comparison" in audit["MATCH EXPLANATION"]


def test_secondary_condition_mismatch_is_not_found(tmp_path: Path):
    result = _secondary_match_result(
        tmp_path,
        [{"Customer Name": "Alpha Consulting", "Posting Date": "2026-09-07", "Gross Amount": 101}],
    )

    assert result["not_found_matches"] == 1
    missing = result["universal_data"]["missing_in_file_2"][0]
    assert missing["IDENTITY CLASSIFICATION"] == "NOT_FOUND"
    assert "No unused destination record" in missing["MATCH EXPLANATION"]


def test_multiple_secondary_candidates_are_ambiguous(tmp_path: Path):
    result = _secondary_match_result(
        tmp_path,
        [
            {"Customer Name": "Alpha Consulting", "Posting Date": "2026-09-07", "Gross Amount": 100},
            {"Customer Name": "Alpaca Consulting", "Posting Date": "2026-09-07", "Gross Amount": 100},
        ],
    )

    assert result["ambiguous_matches"] == 1
    assert result["matched_records"] == 0
    missing = result["universal_data"]["missing_in_file_2"][0]
    assert missing["IDENTITY CLASSIFICATION"] == "AMBIGUOUS_MATCH"
    assert "More than one" in missing["MATCH EXPLANATION"]


def test_secondary_candidate_requires_primary_similarity_threshold(tmp_path: Path):
    result = _secondary_match_result(
        tmp_path,
        [{"Customer Name": "Different Supplier", "Posting Date": "2026-09-07", "Gross Amount": 100}],
    )

    assert result["not_found_matches"] == 1
    audit = result["universal_data"]["identity_resolution"][0]
    assert audit["IDENTITY CLASSIFICATION"] == "NOT_FOUND"
    assert "required 75%" in audit["MATCH EXPLANATION"]


def test_date_only_primary_key_requires_explicit_override(tmp_path: Path):
    file1 = tmp_path / "source.xlsx"
    file2 = tmp_path / "destination.xlsx"
    output = tmp_path / "report.xlsx"
    pd.DataFrame([{"Transaction Date": "07/09/2026", "Amount": 100}]).to_excel(file1, index=False)
    pd.DataFrame([{"Posting Date": "2026-09-07", "Amount": 100}]).to_excel(file2, index=False)

    with pytest.raises(ValueError, match="Date-only matching can be ambiguous"):
        run_generic_reconciliation(
            file1,
            file2,
            output,
            key_file_1="Transaction Date",
            key_file_2="Posting Date",
            rules=[{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}],
        )

    result = run_generic_reconciliation(
        file1,
        file2,
        output,
        key_file_1="Transaction Date",
        key_file_2="Posting Date",
        rules=[{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}],
        date_only_override=True,
    )
    assert result["exact_matches"] == 1
