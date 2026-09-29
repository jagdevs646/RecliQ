"""Bank reconciliation passes: reference key + exact-date secondary key, then an
amount + date ±3 days pass; plus the Differences listing for near-match text."""
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from app.reconciliation_engine.engine import run_generic_reconciliation
from app.reconciliation_engine.matching import compare_values
from app.reconciliation_engine.preprocessing import prepare_dataframe
from app.reconciliation_engine.universal_reporter import generate_enterprise_report

DATE_KEY = {"source_column": "BANK_DATE", "destination_column": "BOOKS_DATE", "comparison_method": "normalized_date"}
DATE_WINDOW_PASS = {
    "type": "amount_date", "amount_source": "DEBIT", "amount_destination": "DEBIT",
    "date_source": "BANK_DATE", "date_destination": "BOOKS_DATE", "date_window_days": 3,
    "narrative_source": "CHEQUE_UTR", "narrative_destination": "REFERENCE",
}
PLAIN_WINDOW_PASS = {key: value for key, value in DATE_WINDOW_PASS.items() if not key.startswith("narrative")}


def _bank(reference: str, date: str, amount: float, **extra) -> dict:
    return {"BANK_DATE": date, "CHEQUE_UTR": reference, "NARRATION": "ORBIT TELECOM SETTLEMENT", "DEBIT": amount, **extra}


def _books(reference: str, date: str, amount: float, **extra) -> dict:
    return {"BOOKS_DATE": date, "REFERENCE": reference, "NARRATION": "ORBIT TELECOM Telecom Expense", "DEBIT": amount, **extra}


def _reco(tmp_path: Path, bank: list[dict], books: list[dict], **options) -> dict:
    options.setdefault("secondary_conditions", [DATE_KEY])
    options.setdefault("matching_passes", [DATE_WINDOW_PASS])
    return run_generic_reconciliation(
        prepare_dataframe(pd.DataFrame(bank)),
        prepare_dataframe(pd.DataFrame(books)),
        tmp_path / "out.xlsx",
        ["CHEQUE_UTR"],
        ["REFERENCE"],
        [{"file_1_fields": ["DEBIT"], "file_2_fields": ["DEBIT"]}],
        write_report=False,
        **options,
    )


def _identity(result: dict, key: str) -> dict:
    return next(item for item in result["universal_data"]["identity_resolution"] if item["MATCH KEY"] == key)


# ── Date window ────────────────────────────────────────────────────────────
def test_same_reference_amount_and_date_is_an_exact_key_match(tmp_path: Path):
    result = _reco(tmp_path, [_bank("UTR26000001", "2026-08-10", 5000)], [_books("UTR26000001", "2026-08-10", 5000)])
    identity = _identity(result, "UTR26000001")
    assert identity["IDENTITY CLASSIFICATION"] == "EXACT_MATCH" and identity["MATCH PASS"] == "Primary key"
    assert result["summary"]["fully_matched_records"] == 1 and result["summary"]["keyless_matches"] == 0


@pytest.mark.parametrize("gap, books_date", [(1, "2026-08-28"), (2, "2026-08-27"), (3, "2026-08-26")])
def test_same_reference_and_amount_within_the_date_window_matches_in_pass_2(tmp_path: Path, gap: int, books_date: str):
    result = _reco(tmp_path, [_bank("UTR26000844", "2026-08-29", 36802.67)], [_books("UTR26000844", books_date, 36802.67)])
    identity = _identity(result, "UTR26000844")
    assert identity["IDENTITY CLASSIFICATION"] == "EXCEPTION_MATCH"  # Dates differ: a person confirms.
    assert identity["MATCH PASS"].startswith("Pass 2")
    assert f"dates {gap} day" in identity["MATCH EXPLANATION"] and "reference 100% similar" in identity["MATCH EXPLANATION"]
    summary = result["summary"]
    assert summary["keyless_matches"] == 1 and summary["only_in_file_1"] == 0 and summary["only_in_file_2"] == 0


def test_a_date_gap_beyond_the_window_stays_unmatched_on_both_sides(tmp_path: Path):
    result = _reco(tmp_path, [_bank("UTR26000846", "2026-08-20", 128425.17)], [_books("UTR26000846", "2026-08-16", 128425.17)])
    identity = _identity(result, "UTR26000846")
    assert identity["IDENTITY CLASSIFICATION"] == "NOT_FOUND"
    assert "BANK_DATE differs" in identity["MATCH EXPLANATION"]
    assert result["summary"]["only_in_file_1"] == 1 and result["summary"]["only_in_file_2"] == 1


def test_a_date_differing_record_pairs_only_with_its_same_reference_partner(tmp_path: Path):
    # The same-reference partner is 9 days out; another books record has the
    # same amount one day out. The pass must not pair across references.
    result = _reco(
        tmp_path,
        [_bank("UTR26000900", "2026-08-29", 1000)],
        [_books("UTR26000900", "2026-08-20", 1000), _books("UTR26000999", "2026-08-28", 1000)],
        matching_passes=[PLAIN_WINDOW_PASS],
    )
    assert _identity(result, "UTR26000900")["IDENTITY CLASSIFICATION"] == "NOT_FOUND"
    assert result["summary"]["only_in_file_2"] == 2


def test_other_must_match_keys_still_block_the_date_window_pass(tmp_path: Path):
    vendor_key = {"source_column": "VENDOR", "destination_column": "VENDOR", "comparison_method": "exact_text"}
    for respect in (True, False):
        result = _reco(
            tmp_path,
            [_bank("UTR26000950", "2026-08-29", 700, VENDOR="Orbit Telecom")],
            [_books("UTR26000950", "2026-08-28", 700, VENDOR="Nimbus Power")],
            secondary_conditions=[DATE_KEY, vendor_key],
            matching_passes=[{**DATE_WINDOW_PASS, "respect_secondary_keys": respect}],
        )
        assert _identity(result, "UTR26000950")["IDENTITY CLASSIFICATION"] == "NOT_FOUND"


def test_the_window_replaces_the_exact_date_key_for_records_without_a_key_partner(tmp_path: Path):
    result = _reco(
        tmp_path, [_bank("NEFT-A1", "2026-08-10", 250.75)], [_books("JV-B7", "2026-08-12", 250.75)],
        matching_passes=[PLAIN_WINDOW_PASS],
    )
    identity = _identity(result, "NEFT-A1")
    assert identity["IDENTITY CLASSIFICATION"] == "EXCEPTION_MATCH" and "dates 2 days apart" in identity["MATCH EXPLANATION"]


def test_two_qualifying_records_in_the_window_are_never_chosen_between(tmp_path: Path):
    result = _reco(
        tmp_path,
        [_bank("NEFT-X1", "2026-08-10", 100)],
        [_books("JV-Y1", "2026-08-11", 100), _books("JV-Y2", "2026-08-09", 100)],
        matching_passes=[PLAIN_WINDOW_PASS],
    )
    assert _identity(result, "NEFT-X1")["IDENTITY CLASSIFICATION"] == "AMBIGUOUS_MATCH"
    assert result["summary"]["only_in_file_2"] == 2


# ── Amounts ────────────────────────────────────────────────────────────────
ROUNDING_RULE = {
    "name": "Small bank differences", "category": "field_difference",
    "conditions": [{"operator": "field_is", "value": "DEBIT"}, {"operator": "difference_abs_lte", "value": 5}],
    "action": {"resolution": "Within tolerance"},
}


def test_small_amount_difference_is_a_value_difference_unless_a_rule_explains_it(tmp_path: Path):
    bank, books = [_bank("UTR26000879", "2026-08-02", 60588.69)], [_books("UTR26000879", "2026-08-02", 60584.71)]
    result = _reco(tmp_path, bank, books)
    assert result["summary"]["field_discrepancies"] == 1
    assert result["universal_data"]["exceptions"][0]["Difference"] == pytest.approx(3.98)

    resolved = _reco(tmp_path, bank, books, resolution_rules=[ROUNDING_RULE])
    assert resolved["summary"]["auto_resolved_differences"] == 1 and resolved["universal_data"]["exceptions"] == []


def test_amount_difference_beyond_the_rule_limit_stays_a_difference(tmp_path: Path):
    result = _reco(
        tmp_path, [_bank("UTR26000880", "2026-08-02", 1050)], [_books("UTR26000880", "2026-08-02", 1000)],
        resolution_rules=[ROUNDING_RULE],
    )
    assert result["summary"]["auto_resolved"] == 0 and result["summary"]["field_discrepancies"] == 1
    assert result["universal_data"]["exceptions"][0]["Difference"] == pytest.approx(50)


def test_same_reference_and_date_with_a_different_amount_is_never_a_clean_match(tmp_path: Path):
    result = _reco(tmp_path, [_bank("AMB260001", "2026-08-02", 15524.34)], [_books("AMB260001", "2026-08-02", 12000)])
    assert result["summary"]["fully_matched_records"] == 0 and result["summary"]["field_discrepancies"] == 1


# ── Unmatched, normalized and grouped records ──────────────────────────────
def test_bank_only_and_books_only_records_stay_unmatched(tmp_path: Path):
    result = _reco(
        tmp_path,
        [_bank("UTR26000001", "2026-08-10", 5000), _bank("BANKONLY260057", "2026-08-31", 15852.92)],
        [_books("UTR26000001", "2026-08-10", 5000), _books("BOOKONLY260069", "2026-08-05", 7852.27)],
    )
    assert _identity(result, "BANKONLY260057")["IDENTITY CLASSIFICATION"] == "NOT_FOUND"
    assert [row["REFERENCE"] for row in result["universal_data"]["missing_in_file_1"]] == ["BOOKONLY260069"]


def test_reference_formatting_is_normalized_before_matching(tmp_path: Path):
    result = _reco(tmp_path, [_bank("UTR/26/000751", "2026-08-10", 900)], [_books("UTR26000751", "2026-08-10", 900)])
    identity = _identity(result, "UTR/26/000751")
    assert identity["IDENTITY CLASSIFICATION"] == "EXACT_MATCH" and identity["CANDIDATE KEY"] == "UTR26000751"


def test_two_books_rows_for_one_bank_transaction_are_added_together(tmp_path: Path):
    result = _reco(
        tmp_path,
        [_bank("GRP260001", "2026-08-15", 35000)],
        [_books("GRP260001", "2026-08-15", 20000), _books("GRP260001", "2026-08-15", 15000)],
    )
    matched = result["universal_data"]["matched_records"]
    assert result["summary"]["fully_matched_records"] == 1
    assert matched[0]["GROUP CLASSIFICATION"] == "One-to-Many Match"


# ── Compared text fields ───────────────────────────────────────────────────
@pytest.mark.parametrize(
    "left, right",
    [("Automobiles & Components", "Automobiles"), ("Diversified Banks", "Banks"), ("Oil, Gas & Consumable Fuels", "Oil & Gas")],
)
def test_text_contained_in_another_text_is_not_identical(left: str, right: str):
    assert compare_values(left, right, "INDUSTRY", "INDUSTRY", "text").confidence < 100


def test_near_match_text_differences_are_listed_not_just_counted(tmp_path: Path):
    result = run_generic_reconciliation(
        prepare_dataframe(pd.DataFrame([
            {"Ticker": "MYPK3 BZ", "Industry": "Automobiles & Components"},
            {"Ticker": "FRAS3 BZ", "Industry": "Automobiles & Components"},
            {"Ticker": "BEEF3 BZ", "Industry": "Food Products"},
        ])),
        prepare_dataframe(pd.DataFrame([
            {"Ticker": "MYPK3 BZ", "Industry": "Automobile Components"},
            {"Ticker": "FRAS3 BZ", "Industry": "Automobiles"},
            {"Ticker": "BEEF3 BZ", "Industry": "Food Products"},
        ])),
        tmp_path / "out.xlsx", ["Ticker"], ["Ticker"], [{"file_1_fields": ["Industry"], "file_2_fields": ["Industry"]}],
        write_report=False,
    )
    data = result["universal_data"]
    assert result["summary"]["field_discrepancies"] == 2
    assert sorted(row["Primary Key"] for row in data["exceptions"]) == ["FRAS3 BZ", "MYPK3 BZ"]
    near = next(row for row in data["exceptions"] if row["Primary Key"] == "MYPK3 BZ")
    assert near["Similarity"] == "Minor spelling variation (89%)"

    output = tmp_path / "report.xlsx"
    generate_enterprise_report(data, {}, output)
    sheet = openpyxl.load_workbook(output)["02 Differences"]
    header = [cell.value for cell in sheet[4]]
    rows = [row for row in sheet.iter_rows(min_row=5, values_only=True) if row[0]]
    assert "Similarity" in header and len(rows) == 2
