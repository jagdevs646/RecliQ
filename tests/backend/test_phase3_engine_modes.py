from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts" / "perf"))

from app.reconciliation_engine.engine import run_generic_reconciliation
from result_comparison import compare_result_sets


def test_column_engine_exact_fast_path_preserves_universal_results(tmp_path: Path) -> None:
    file_1 = pd.DataFrame({
        "INVOICE": ["INV-1", "INV-2", "INV-3", "INV-4"],
        "BRANCH": ["North", "South", "West", "East"],
        "AMOUNT": [100, 200, 300, 400],
    })
    file_2 = pd.DataFrame({
        "INVOICE NO": ["INV/3", "INV-1", "INV-2", "INV-5"],
        "BRANCH NAME": ["West", "North", "South", "Central"],
        "VALUE": [301, 100, 200, 500],
    })
    options = {
        "key_file_1": ["INVOICE"],
        "key_file_2": ["INVOICE NO"],
        "rules": [{"file_1_fields": ["AMOUNT"], "file_2_fields": ["VALUE"]}],
        "secondary_conditions": [{
            "source_column": "BRANCH",
            "destination_column": "BRANCH NAME",
            "comparison_method": "exact_text",
        }],
        "write_report": False,
    }

    row_result = run_generic_reconciliation(
        file_1.copy(), file_2.copy(), tmp_path / "rows.xlsx", engine_mode="rows", **options,
    )
    column_result = run_generic_reconciliation(
        file_1.copy(), file_2.copy(), tmp_path / "columns.xlsx", engine_mode="columns", **options,
    )

    assert compare_result_sets(row_result["universal_data"], column_result["universal_data"]) == []
    assert column_result["universal_data"]["metadata"]["engine"] == "columns"
    assert column_result["universal_data"]["metadata"]["engine_version"] == "columns-v1"


def test_column_text_batch_preserves_normalization_and_fuzzy_result_details(tmp_path: Path) -> None:
    file_1 = pd.DataFrame({
        "ID": ["1", "2", "3", "4"],
        "EMPLOYEE NAME": ["Jagdev Singh", "Softwre Labs", "Alpha Partners", "ABC Pvt Ltd"],
    })
    file_2 = pd.DataFrame({
        "ID": ["4", "2", "1", "3"],
        "EMPLOYEE NAME": ["ABC Private Limited", "Software Labs", "Singh Jagdev", "Completely Different"],
    })
    options = {
        "key_file_1": ["ID"],
        "key_file_2": ["ID"],
        "rules": [{"file_1_fields": ["EMPLOYEE NAME"], "file_2_fields": ["EMPLOYEE NAME"]}],
        "write_report": False,
    }

    row_result = run_generic_reconciliation(
        file_1.copy(), file_2.copy(), tmp_path / "text-rows.xlsx", engine_mode="rows", **options,
    )
    column_result = run_generic_reconciliation(
        file_1.copy(), file_2.copy(), tmp_path / "text-columns.xlsx", engine_mode="columns", **options,
    )

    assert compare_result_sets(row_result["universal_data"], column_result["universal_data"]) == []
    column_data = column_result["universal_data"]
    assert len(column_data["matched_records"]) == 2
    assert any("Word order ignored" in row.get("NORMALIZATION APPLIED", "") for row in column_data["matched_records"])
    assert len(column_data["exceptions"]) == 2
    fuzzy = [row for row in column_data["exceptions"] if row.get("Similarity")]
    assert len(fuzzy) == 1
    assert fuzzy[0]["Similarity"].startswith("Minor spelling variation")


def test_column_numeric_batch_preserves_rounding_blanks_and_differences(tmp_path: Path) -> None:
    file_1 = pd.DataFrame({
        "ID": ["A", "B", "C", "D"],
        "AMOUNT": [2.675, 1.005, 10.25, None],
    })
    file_2 = pd.DataFrame({
        "ID": ["A", "B", "C", "D"],
        "VALUE": [2.68, 1.00, 10.25, 1.0],
    })
    options = {
        "key_file_1": ["ID"],
        "key_file_2": ["ID"],
        "rules": [{"file_1_fields": ["AMOUNT"], "file_2_fields": ["VALUE"], "decimal_precision": 2}],
        "write_report": False,
    }

    row_result = run_generic_reconciliation(
        file_1.copy(), file_2.copy(), tmp_path / "numeric-rows.xlsx", engine_mode="rows", **options,
    )
    column_result = run_generic_reconciliation(
        file_1.copy(), file_2.copy(), tmp_path / "numeric-columns.xlsx", engine_mode="columns", **options,
    )

    assert compare_result_sets(row_result["universal_data"], column_result["universal_data"]) == []
    differences = {row["Primary Key"]: row for row in column_result["universal_data"]["exceptions"]}
    assert differences["B"]["Difference"] == pytest.approx(0.005)
    assert differences["D"]["Difference"] == pytest.approx(-1.0)


def test_column_identifier_batch_preserves_formatting_confidence_and_exactness(tmp_path: Path) -> None:
    file_1 = pd.DataFrame({
        "ID": ["1", "2", "3"],
        "INVOICE NO": ["INV-005", "INV005", "INV/010"],
    })
    file_2 = pd.DataFrame({
        "ID": ["1", "2", "3"],
        "INVOICE NUMBER": ["INV005", "INV007", "INV-010"],
    })
    options = {
        "key_file_1": ["ID"],
        "key_file_2": ["ID"],
        "rules": [{
            "file_1_fields": ["INVOICE NO"],
            "file_2_fields": ["INVOICE NUMBER"],
            "comparison_type": "invoice",
        }],
        "write_report": False,
    }

    row_result = run_generic_reconciliation(
        file_1.copy(), file_2.copy(), tmp_path / "identifier-rows.xlsx", engine_mode="rows", **options,
    )
    column_result = run_generic_reconciliation(
        file_1.copy(), file_2.copy(), tmp_path / "identifier-columns.xlsx", engine_mode="columns", **options,
    )

    assert compare_result_sets(row_result["universal_data"], column_result["universal_data"]) == []
    exceptions = column_result["universal_data"]["exceptions"]
    assert len(exceptions) == 2
    assert any(item.get("Similarity") == "Identifier formatting difference only (97%)" for item in exceptions)


def test_column_date_batch_preserves_row_mismatch_and_invalid_fallback(tmp_path: Path) -> None:
    file_1 = pd.DataFrame({
        "ID": ["A", "B", "C"],
        "INVOICE DATE": ["2026-01-01", "2026-02-01", "invalid"],
    })
    file_2 = pd.DataFrame({
        "ID": ["A", "B", "C"],
        "DATE": ["01/01/2026", "2026-02-02", "invalid"],
    })
    options = {
        "key_file_1": ["ID"],
        "key_file_2": ["ID"],
        "rules": [{
            "file_1_fields": ["INVOICE DATE"],
            "file_2_fields": ["DATE"],
            "comparison_type": "date",
        }],
        "write_report": False,
    }

    row_result = run_generic_reconciliation(
        file_1.copy(), file_2.copy(), tmp_path / "date-rows.xlsx", engine_mode="rows", **options,
    )
    column_result = run_generic_reconciliation(
        file_1.copy(), file_2.copy(), tmp_path / "date-columns.xlsx", engine_mode="columns", **options,
    )

    assert compare_result_sets(row_result["universal_data"], column_result["universal_data"]) == []
    exceptions = column_result["universal_data"]["exceptions"]
    assert len(exceptions) == 1
    assert exceptions[0]["File 1 Value"] == "2026-02-01"
    assert exceptions[0]["File 2 Value"] == "2026-02-02"
    assert "Similarity" not in exceptions[0]


def test_generic_engine_rejects_unknown_engine_mode(tmp_path: Path) -> None:
    frame = pd.DataFrame({"ID": ["A"], "Amount": [1]})

    with pytest.raises(ValueError, match="engine must be either 'rows' or 'columns'"):
        run_generic_reconciliation(
            frame.copy(), frame.copy(), tmp_path / "unused.xlsx",
            key_file_1=["ID"], key_file_2=["ID"],
            rules=[{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}],
            write_report=False, engine_mode="unknown",
        )


def test_engine_environment_variable_selects_column_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ENGINE", "columns")
    frame = pd.DataFrame({"ID": ["A"], "AMOUNT": [1]})

    result = run_generic_reconciliation(
        frame.copy(), frame.copy(), tmp_path / "unused.xlsx",
        key_file_1=["ID"], key_file_2=["ID"],
        rules=[{"file_1_fields": ["AMOUNT"], "file_2_fields": ["AMOUNT"]}],
        write_report=False,
    )

    assert result["universal_data"]["metadata"]["engine"] == "columns"
