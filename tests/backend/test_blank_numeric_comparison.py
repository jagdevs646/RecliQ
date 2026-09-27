"""A blank cell compared with a number counts as 0, for numeric fields only."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

import pandas as pd
import pytest

from app.reconciliation.generic import run_generic_reconciliation
from app.reconciliation_engine.engine import compare_rule_values


def _compare(column_1: str, value_1, column_2: str, value_2) -> dict:
    return compare_rule_values({column_1: value_1}, {column_2: value_2}, [column_1], [column_2])


@pytest.mark.parametrize("blank", ["", None, float("nan"), "  "])
@pytest.mark.parametrize("zero", [0, 0.0, "0", "0.00"])
def test_zero_equals_blank_in_either_direction(zero, blank):
    assert _compare("AMOUNT", zero, "AMOUNT", blank) == {}
    assert _compare("AMOUNT", blank, "AMOUNT", zero) == {}


def test_type_comes_from_the_populated_side_even_without_a_numeric_column_name():
    assert _compare("ADJUSTMENT", 0, "ADJ", "") == {}


def test_non_zero_against_blank_is_a_numeric_difference():
    differences = _compare("AMOUNT", 250, "AMOUNT", "")
    assert differences["AMOUNT DIFF"] == 250
    assert differences["AMOUNT STATUS"] == "Mismatch"
    # The report shows what the file really contains, not the assumed 0.
    assert differences["AMOUNT (FILE 2)"] == ""

    differences = _compare("AMOUNT", None, "AMOUNT", -40.5)
    assert differences["AMOUNT DIFF"] == 40.5


def test_both_blank_still_match():
    assert _compare("AMOUNT", "", "AMOUNT", None) == {}


def test_text_and_date_fields_are_not_treated_as_zero():
    # Text against blank is still a difference.
    assert _compare("REMARKS", "paid", "REMARKS", "")["REMARKS STATUS"] == "Mismatch"
    # A date column is compared as a date/text, never as 0.
    assert _compare("POSTING DATE", "2026-01-05", "POSTING DATE", "") != {}
    # Identifiers are not amounts: invoice "0" vs blank is a missing value.
    assert _compare("INVOICE NO", "0", "INVOICE NO", "") != {}
    assert _compare("REFERENCE ID", 0, "REFERENCE ID", "") != {}


def test_numeric_comparison_is_unchanged_when_both_sides_have_values():
    assert _compare("AMOUNT", 100, "AMOUNT", "100.00") == {}
    assert _compare("AMOUNT", 100, "AMOUNT", 90)["AMOUNT DIFF"] == 10


def test_reconciliation_treats_zero_and_blank_amounts_as_equal(tmp_path: Path):
    file1, file2, output = tmp_path / "books.xlsx", tmp_path / "bank.xlsx", tmp_path / "report.xlsx"
    pd.DataFrame([
        {"Ref": "A1", "Amount": 100, "Discount": 0, "Note": "ok"},
        {"Ref": "A2", "Amount": 200, "Discount": None, "Note": ""},
        {"Ref": "A3", "Amount": 300, "Discount": 15, "Note": "x"},
        {"Ref": "A4", "Amount": 400, "Discount": 0, "Note": "late"},
    ]).to_excel(file1, index=False)
    pd.DataFrame([
        {"Ref": "A1", "Amount": 100, "Discount": None, "Note": "ok"},
        {"Ref": "A2", "Amount": 200, "Discount": 0, "Note": ""},
        {"Ref": "A3", "Amount": 300, "Discount": None, "Note": "x"},
        {"Ref": "A4", "Amount": 400, "Discount": 0, "Note": None},
    ]).to_excel(file2, index=False)

    summary = run_generic_reconciliation(
        file1, file2, output,
        key_file_1="Ref", key_file_2="Ref",
        rules=[
            {"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]},
            {"file_1_fields": ["Discount"], "file_2_fields": ["Discount"]},
            {"file_1_fields": ["Note"], "file_2_fields": ["Note"]},
        ],
    )

    # A1 and A2 (0 vs blank) match; A3 (15 vs blank) and A4 (text vs blank) do not.
    assert summary["matched_records"] == 4
    assert summary["fully_matched_records"] == 2
    assert summary["field_discrepancies"] == 2
