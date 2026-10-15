from __future__ import annotations

import numpy as np
import pandas as pd

from app.reconciliation_engine import column_engine
from app.reconciliation_engine.column_engine import (
    compare_identifier_pairs,
    compare_date_pairs,
    compare_numeric_pairs,
    compare_text_pairs,
    unique_exact_pairs,
)
from app.reconciliation_engine.matching import indexed_matcher
from app.reconciliation_engine.matching import compare_values
from app.reconciliation_engine.matching.indexed_matcher import IndexedCandidateMatcher
from app.reconciliation_engine.matching import pass_matcher
from app.reconciliation_engine.normalization import build_normalizer, use_normalizer
from app.reconciliation_engine.matching.pass_matcher import run_column_pass, run_pass


def test_unique_exact_pairs_only_returns_complete_one_to_one_keys() -> None:
    file_1 = pd.DataFrame(
        {"__KEY_0__": ["A", "B", "DUP", "DUP", None]},
        index=[10, 11, 12, 13, 14],
    )
    file_2 = pd.DataFrame(
        {"__KEY_0__": ["B", "A", "DUP", None]},
        index=[90, 91, 92, 93],
    )

    assert unique_exact_pairs(file_1, file_2, ["__KEY_0__"], ["__KEY_0__"]) == [(0, 1), (1, 0)]


def test_unique_exact_pairs_supports_composite_keys_and_empty_inputs() -> None:
    file_1 = pd.DataFrame({"__KEY_0__": ["INV-1", "INV-1"], "__KEY_1__": ["A", "B"]})
    file_2 = pd.DataFrame({"__KEY_0__": ["INV-1", "INV-1"], "__KEY_1__": ["B", "A"]})

    assert unique_exact_pairs(
        file_1, file_2, ["__KEY_0__", "__KEY_1__"], ["__KEY_0__", "__KEY_1__"]
    ) == [(0, 1), (1, 0)]
    assert unique_exact_pairs(file_1, file_2, [], []) == []


def test_column_keyless_pass_matches_row_pass_for_amount_date_and_narrative() -> None:
    config = {
        "type": "amount_tolerance",
        "amount_source": "AMOUNT",
        "amount_destination": "AMOUNT",
        "amount_tolerance": 1.0,
        "date_source": "DATE",
        "date_destination": "DATE",
        "date_window_days": 3,
        "narrative_source": "NOTE",
        "narrative_destination": "DETAIL",
        "narrative_threshold": 75,
    }
    sources = [
        (4, {"AMOUNT": 100, "DATE": "2026-01-01", "NOTE": "Acme payment March"}),
        (9, {"AMOUNT": 200, "DATE": "2026-01-03", "NOTE": "Vendor refund reference 22"}),
    ]
    destinations = [
        (21, {"AMOUNT": 100, "DATE": "2026-01-01", "DETAIL": "Acme payment March"}),
        (22, {"AMOUNT": 200.5, "DATE": "2026-01-05", "DETAIL": "Vendor refund reference 22"}),
        (23, {"AMOUNT": 350, "DATE": "2026-01-01", "DETAIL": "Unrelated"}),
    ]

    assert run_column_pass(config, sources, destinations) == run_pass(config, sources, destinations)


def test_candidate_cap_is_applied_before_date_filtering() -> None:
    config = {
        "type": "amount_date",
        "amount_source": "AMOUNT",
        "amount_destination": "AMOUNT",
        "date_source": "DATE",
        "date_destination": "DATE",
        "date_window_days": 1,
    }
    source = [(0, {"AMOUNT": 100, "DATE": "2026-01-01"})]
    destinations = [
        (index, {"AMOUNT": 100, "DATE": "2026-02-01"})
        for index in range(5001)
    ]
    destinations.append((5001, {"AMOUNT": 100, "DATE": "2026-01-01"}))

    row_result = run_pass(config, source, destinations)
    column_result = run_column_pass(config, source, destinations)

    assert row_result == column_result
    assert row_result[0] == []
    assert row_result[1][0].candidate_count == 5000
    assert "too many" in row_result[1][0].detail


def test_narrative_batch_skips_normalization_for_exact_and_blank_pairs(monkeypatch) -> None:
    def unexpected_normalization():
        raise AssertionError("exact and blank narratives should not be normalized")

    monkeypatch.setattr(pass_matcher, "active_normalizer", unexpected_normalization)

    assert pass_matcher._narrative_scores(["same", "", "nan"], ["same", "description", "NAN"]) == [
        100, 0, 0,
    ]


def test_exact_only_narrative_threshold_skips_unreachable_fuzzy_scores(monkeypatch) -> None:
    def unexpected_fuzzy_scoring(*_args, **_kwargs):
        raise AssertionError("different text cannot pass a 100% narrative threshold")

    monkeypatch.setattr(pass_matcher.process, "cpdist", unexpected_fuzzy_scoring)

    assert pass_matcher._narrative_scores(
        ["same", "Payment reference 100"],
        ["same", "Payment reference 101"],
        exact_only=True,
    ) == [100, 0]


def test_column_text_comparison_matches_row_scores_and_normalization_rules() -> None:
    left = ["Jagdev Singh", "ABC Pvt Ltd", "Softwre Labs", "unrelated source"]
    right = ["Singh Jagdev", "ABC Private Limited", "Software Labs", "different destination"]
    normalizer = build_normalizer(None, ())

    with use_normalizer(normalizer):
        batch = compare_text_pairs(left, right, normalizer)
        assert batch is not None
        for index, (value_1, value_2) in enumerate(zip(left, right)):
            expected = compare_values(value_1, value_2, matcher_type="text")
            if batch.equal[index]:
                assert expected.matched and expected.confidence == 100
            else:
                assert int(batch.scores[index]) == expected.confidence

    assert "Word order ignored" in batch.normalization_rules[0]
    assert batch.equal[1]
    assert batch.scores[2] >= 85
    assert batch.scores[3] < 75


def test_column_text_comparison_declines_mixed_values_for_row_fallback() -> None:
    normalizer = build_normalizer(None, ())
    assert compare_text_pairs(["Acme", 42], ["Acme", "42"], normalizer) is None


def test_column_text_comparison_scores_repeated_canonical_pairs_once(monkeypatch) -> None:
    normalizer = build_normalizer(None, ())
    original_cpdist = column_engine.process.cpdist
    scored_batch_sizes: list[int] = []

    def record_batch_size(choices, queries, **kwargs):
        scored_batch_sizes.append(len(choices))
        return original_cpdist(choices, queries, **kwargs)

    monkeypatch.setattr(column_engine.process, "cpdist", record_batch_size)
    left = ["softwre services 2025"] * 4 + ["acme logistics invoice 8"] * 3
    right = ["software services 2026"] * 4 + ["acme logistics invoice 9"] * 3

    batch = compare_text_pairs(left, right, normalizer)

    assert batch is not None
    assert scored_batch_sizes == [2] * len(column_engine._TEXT_SCORERS)
    assert len(set(batch.scores.tolist())) == 2
    for index, (value_1, value_2) in enumerate(zip(left, right)):
        assert int(batch.scores[index]) == compare_values(value_1, value_2, matcher_type="text").confidence


def test_secondary_matcher_caches_repeated_value_pairs(monkeypatch) -> None:
    candidates = pd.DataFrame({
        "KEY": [f"K{index}" for index in range(100)],
        "PAYEE": ["Acme Traders"] * 80 + ["Orbit Logistics"] * 20,
    })
    matcher = IndexedCandidateMatcher(candidates, "KEY", "identifier", build_fuzzy_indexes=False)
    original_compare = indexed_matcher.compare_values
    compared_pairs: list[tuple[object, object]] = []

    def count_comparisons(value_1, value_2, *args, **kwargs):
        compared_pairs.append((value_1, value_2))
        return original_compare(value_1, value_2, *args, **kwargs)

    monkeypatch.setattr(indexed_matcher, "compare_values", count_comparisons)
    positions, _ = matcher._candidate_positions_for_condition({
        "source_value": "Acme Traders",
        "destination_column": "PAYEE",
        "comparison_method": "matcher_based",
    })

    assert len(positions) == 80
    assert compared_pairs == [("Acme Traders", "Acme Traders")]


def test_column_numeric_comparison_uses_half_up_rounding_and_preserves_raw_difference() -> None:
    batch = compare_numeric_pairs(
        ["2,675", "1,005", "1,25", "", "not numeric"],
        ["2,68", "1,00", "1,24", "3", "4"],
        decimal_precision=2,
        number_format_1="decimal_comma",
        number_format_2="decimal_comma",
    )

    assert batch is not None
    assert batch.valid.tolist() == [True, True, True, False, False]
    assert batch.equal.tolist() == [True, False, False, False, False]
    assert batch.differences[1] == 0.005
    assert batch.differences[2] == 0.01


def test_column_numeric_float_fast_path_preserves_decimal_boundaries_and_differences() -> None:
    values_1 = [1.005, -1.005, 1000.0, 4.0]
    values_2 = [1.0, -1.0, 1000.00025, 4.001]
    fast = compare_numeric_pairs(values_1, values_2, 2)
    reference = compare_numeric_pairs(values_1, values_2, 2, "decimal_comma", "decimal_comma")

    assert fast is not None and reference is not None
    assert fast.valid.tolist() == reference.valid.tolist()
    assert fast.equal.tolist() == reference.equal.tolist()
    np.testing.assert_equal(fast.differences, reference.differences)


def test_column_identifier_comparison_never_fuzzily_matches_different_ids() -> None:
    batch = compare_identifier_pairs(
        ["INV-005", "INV005", "INV/010", "", "ABC"],
        ["INV005", "INV007", "INV-010", None, "ABCD"],
    )

    assert batch is not None
    assert batch.confidence.tolist() == [97, 0, 100, 100, 0]


def test_column_date_comparison_batches_valid_dates_and_defers_invalid_values() -> None:
    batch = compare_date_pairs(
        ["2026-01-01", "not a date", "2026-02-01"],
        ["01/01/2026", "not a date", "2026-02-02"],
    )

    assert batch is not None
    assert batch.valid.tolist() == [True, False, True]
    assert batch.equal.tolist() == [True, False, False]
