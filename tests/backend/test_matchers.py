import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

import pandas as pd
import pytest

from app.reconciliation.matchers import IndexedCandidateMatcher, compare_values, parse_date_value, prepare_dataframe
from app.reconciliation_engine.matching.indexed_matcher import IndexedCandidateMatcher as EngineCandidateMatcher
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


def test_generic_index_mode_builds_only_identity_and_secondary_candidate_maps():
    matcher = EngineCandidateMatcher(
        pd.DataFrame({"INVOICE": ["INV-001", "INV-002"]}),
        "INVOICE",
        "invoice",
        build_fuzzy_indexes=False,
    )

    assert matcher.identity_map
    assert matcher.compact_map
    assert matcher.token_blocks
    assert matcher.find_exact_candidates("INV/001")[0][0] == 0
    assert matcher.exact_map == {}
    assert matcher.token_key_map == {}
    assert matcher.synonym_key_map == {}
    assert matcher.date_map == {}


def test_default_index_mode_retains_maps_used_by_gst_best_match():
    matcher = EngineCandidateMatcher(
        pd.DataFrame({"INVOICE": ["INV-001", "INV-002"]}),
        "INVOICE",
        "invoice",
    )

    assert matcher.exact_map
    assert matcher.compact_map
    assert matcher.token_key_map
    assert matcher.synonym_key_map
    assert matcher.token_blocks
    index, row, result = matcher.find_best_match("INV/001", "INVOICE")
    assert index == 0
    assert row is not None and row["INVOICE"] == "INV-001"
    assert result is not None and result.matched


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


def test_identifiers_and_dates_are_never_parsed_as_numbers():
    from app.reconciliation_engine.cache import to_number
    from app.reconciliation_engine.engine import compare_rule_values

    assert to_number("INR 1,20,000") == 120000.0
    assert to_number("(250.50)") == -250.5
    # Previously "S-3" parsed as -3.0 and "07/09/2026" as 7092026.0.
    assert to_number("S-3") is None
    assert to_number("INV-005") is None
    assert to_number("07/09/2026") is None
    # A formatting-only identifier difference is text, never a numeric DIFF.
    differences = compare_rule_values({"INVOICE": "S-3"}, {"INVOICE": "S3"}, ["INVOICE"], ["INVOICE"])
    assert "INVOICE DIFF" not in differences


def test_identical_values_as_read_skip_type_detection():
    from app.reconciliation_engine.engine import compare_rule_values

    with patch("app.reconciliation_engine.engine.detect_matcher_type", side_effect=AssertionError):
        assert compare_rule_values(
            {"NARRATION": "Rent for March 2026"},
            {"NARRATION": "Rent for March 2026"},
            ["NARRATION"],
            ["NARRATION"],
        ) == {}


def test_identical_normalized_text_is_not_enough_for_the_shortcut():
    from app.reconciliation_engine.engine import compare_rule_values

    differences = compare_rule_values(
        {"AMOUNT": "-500"}, {"AMOUNT": "500"}, ["AMOUNT"], ["AMOUNT"]
    )

    assert differences["AMOUNT DIFF"] == -1000.0


@pytest.mark.parametrize(
    ("method", "source_value", "destination_values"),
    [
        ("exact_text", "ABC PVT LTD", ["ABC PRIVATE LIMITED", "Other Ltd"]),
        ("normalized_date", "07/09/2026", ["2026-09-07", "2026-09-08"]),
        ("numeric_tolerance", 100, [100.5, 103]),
        ("matcher_based", "Alpha Consulting", ["Alpha Consulting Limited", "Beta Ltd"]),
    ],
)
def test_direct_secondary_pair_check_matches_indexed_candidates(method, source_value, destination_values):
    matcher = EngineCandidateMatcher(
        pd.DataFrame([{"KEY": f"INV-{index}", "DESTINATION": value} for index, value in enumerate(destination_values)]),
        "KEY",
        "invoice",
    )
    condition = {
        "source_column": "SOURCE",
        "destination_column": "DESTINATION",
        "comparison_method": method,
        "numeric_tolerance": 1,
    }
    source_row = {"SOURCE": source_value}
    indexed, _ = matcher.find_secondary_candidates(source_row, [condition])
    indexed_positions = {position for position, _ in indexed}

    assert indexed_positions == {0}
    assert {
        position
        for position, row in enumerate(matcher.rows)
        if matcher.matches_conditions(source_row, row, [condition])
    } == indexed_positions


def test_bounded_secondary_search_caches_values_and_narrows_large_groups(monkeypatch):
    rows = [
        {"KEY": "ZZ-9001", "DESTINATION": "Shared branch"},
        {"KEY": "ZZ-9002", "DESTINATION": "Shared branch"},
        *({"KEY": f"INV-{index:03d}", "DESTINATION": "Shared branch"} for index in range(2, 40)),
    ]
    matcher = EngineCandidateMatcher(pd.DataFrame(rows), "KEY", "invoice")
    source_row = {"SOURCE": "Shared branch"}
    conditions = [{
        "source_column": "SOURCE",
        "destination_column": "DESTINATION",
        "comparison_method": "exact_text",
    }]
    used_indices = {0}
    indexed, _ = matcher.find_secondary_candidates(source_row, conditions, used_indices)
    token_indices = matcher.token_candidate_indices("ZZ-9002")
    expected = [candidate for candidate in indexed if candidate[0] in token_indices]

    calls = 0
    original = matcher._candidate_positions_for_condition

    def count_condition_index_builds(condition):
        nonlocal calls
        calls += 1
        return original(condition)

    monkeypatch.setattr(matcher, "_candidate_positions_for_condition", count_condition_index_builds)
    first = matcher.bounded_secondary_candidates(source_row, conditions, used_indices, 25, "ZZ-9002")
    second = matcher.bounded_secondary_candidates(source_row, conditions, used_indices, 25, "ZZ-9002")

    assert [index for index, _ in first] == [index for index, _ in expected] == [1]
    assert [index for index, _ in second] == [index for index, _ in expected]
    assert calls == 1
