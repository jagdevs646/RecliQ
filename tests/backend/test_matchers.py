import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

import pandas as pd

from app.reconciliation.matchers import IndexedCandidateMatcher, compare_values, parse_date_value, prepare_dataframe
from app.reconciliation_engine.normalization import normalize_date_series


def test_invoice_formatting_difference_scores_high():
    result = compare_values("INV-005", "inv005", "Invoice No", "Invoice_Number")
    assert result.matched
    assert result.confidence >= 95
    assert result.matcher_type == "invoice"


def test_business_synonyms_match_company_names():
    result = compare_values("ABC PVT LTD", "ABC PRIVATE LIMITED", "Name", "Company Name", "company_name")
    assert result.matched
    assert result.confidence == 100


def test_horizontal_orientation_is_transformed():
    source = pd.DataFrame(
        {
            "Field": ["Invoice", "Amount"],
            "Record1": ["INV001", 1000],
            "Record2": ["INV002", 2000],
        }
    )
    result = prepare_dataframe(source, orientation="horizontal")
    assert list(result.columns) == ["INVOICE", "AMOUNT"]
    assert len(result) == 2


def test_indexed_matcher_uses_every_composite_key_component():
    candidates = pd.DataFrame(
        [
            {"Invoice": "INV-100", "Entity": "North", "Amount": 100},
            {"Invoice": "INV-100", "Entity": "South", "Amount": 200},
        ]
    )
    matcher = IndexedCandidateMatcher(
        candidates,
        ["Invoice", "Entity"],
        ["invoice", "text"],
    )

    index, row, result = matcher.find_best_match(["INV100", "South"], ["Invoice", "Entity"])

    assert index == 1
    assert row is not None and row["Entity"] == "South"
    assert result is not None
    assert result.matcher_type == "composite"
    assert result.status == "Exact composite key match"


def test_canonical_date_parser_uses_one_day_first_convention():
    assert parse_date_value("07/09/2026") == date(2026, 9, 7)
    assert parse_date_value("2026-09-07") == date(2026, 9, 7)
    assert parse_date_value("7 Sep 2026") == date(2026, 9, 7)
    assert parse_date_value(datetime(2026, 9, 7, 14, 30)) == date(2026, 9, 7)
    assert parse_date_value(pd.Timestamp("2026-09-07")) == date(2026, 9, 7)
    assert parse_date_value("not a date") is None


def test_date_series_and_row_comparison_share_canonical_normalization():
    normalized = normalize_date_series(pd.Series(["07/09/2026", "7 September 2026", "not a date"]))

    assert normalized.tolist() == ["2026-09-07", "2026-09-07", "not a date"]
    result = compare_values("07/09/2026", "2026-09-07", "Transaction Date", "Posting Date")
    assert result.matched
    assert result.confidence == 100
