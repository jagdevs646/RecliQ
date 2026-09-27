"""Business-name normalization, aliases, transformations and keyless passes."""
from pathlib import Path

import pandas as pd
import pytest

from app.reconciliation_engine.engine import run_generic_reconciliation
from app.reconciliation_engine.matching import compare_values
from app.reconciliation_engine.normalization.entities import EntityNormalizer, NormalizerConfig
from app.reconciliation_engine.preprocessing import prepare_dataframe


def _run(tmp_path: Path, source: list[dict], destination: list[dict], **options) -> dict:
    options.setdefault("rules", [{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}])
    return run_generic_reconciliation(
        prepare_dataframe(pd.DataFrame(source)),
        prepare_dataframe(pd.DataFrame(destination)),
        tmp_path / "out.xlsx",
        write_report=False,
        **options,
    )


def _identity(result: dict) -> list[dict]:
    return result["universal_data"]["identity_resolution"]


# ── Deterministic normalization ─────────────────────────────────────────────
@pytest.mark.parametrize(
    "left, right, expected_rules",
    [
        ("ABC Pvt Ltd", "ABC PRIVATE LIMITED", ["Pvt → Private", "Ltd → Limited"]),
        ("Tata & Sons Co", "TATA AND SONS COMPANY", ["& → And", "Co → Company"]),
        ("Acme Corp", "Acme Corporation", ["Corp → Corporation"]),
        ("Globex Inc.", "GLOBEX INCORPORATED", ["Inc → Incorporated"]),
        ("M/s A.B.C. (P) Ltd.", "ABC Private Limited", ["Ignored 'M/s'", "(P) → Private"]),
        ("Limited ABC Private", "ABC Private Limited", ["Word order ignored"]),
        ("Intl Traders", "International Traders", ["Intl → International"]),
    ],
)
def test_equivalent_business_names_share_one_identity(left, right, expected_rules):
    normalizer = EntityNormalizer()
    assert normalizer.equivalent(left, right)
    assert set(expected_rules) <= set(normalizer.explain(left, right))


def test_different_legal_forms_and_ambiguous_abbreviations_stay_different():
    normalizer = EntityNormalizer()
    assert not normalizer.equivalent("ABC Ltd", "ABC Pvt Ltd")  # Different legal entities.
    assert not normalizer.equivalent("Int Payment", "International Payment")  # "Int" is ambiguous.
    assert not normalizer.equivalent("ABC Traders", "ABD Traders")


def test_configured_synonyms_and_aliases_are_named_in_the_explanation():
    normalizer = EntityNormalizer(NormalizerConfig(synonyms=(("tech", "technologies"),), aliases=(("IBM", "International Business Machines Corp"),)))
    assert normalizer.equivalent("Infy Tech", "Infy Technologies")
    assert normalizer.equivalent("IBM", "International Business Machines Corporation")
    assert any(rule.startswith("Alias: IBM") for rule in normalizer.explain("IBM", "International Business Machines Corporation"))


def test_text_comparison_reports_the_rule_and_keeps_fuzzy_for_review():
    exact = compare_values("ABC Pvt Ltd", "ABC PRIVATE LIMITED", "Vendor", "Vendor", "company_name")
    assert exact.matched and exact.confidence == 100 and "Pvt → Private" in exact.rules
    fuzzy = compare_values("ABC Traders Pvt Ltd", "ABC Tradrs Private Limited", "Vendor", "Vendor", "company_name")
    assert fuzzy.confidence < 100  # A typo is never an exact match.


# ── Engine integration ─────────────────────────────────────────────────────
def test_name_secondary_key_matches_after_normalization_with_explanation(tmp_path: Path):
    """The user's example: ID + Name, where the names differ only in legal form."""
    result = _run(
        tmp_path,
        [{"ID": "INV005", "Name": "ABC Pvt Ltd", "Amount": 100}],
        [{"ID": "INV/005", "Name": "ABC PRIVATE LIMITED", "Amount": 100}],
        key_file_1=["ID"], key_file_2=["ID"],
        secondary_conditions=[{"source_column": "Name", "destination_column": "Name", "comparison_method": "exact_text"}],
    )
    assert result["summary"]["exact_matches"] == 1 and result["summary"]["only_in_file_1"] == 0
    identity = _identity(result)[0]
    assert "after normalization" in identity["MATCH EXPLANATION"]
    assert "Pvt → Private" in identity["NORMALIZATION APPLIED"]
    matched = result["universal_data"]["matched_records"][0]
    assert "Ltd → Limited" in matched["NORMALIZATION APPLIED"]


def test_name_primary_key_and_grouping_use_the_normalized_identity(tmp_path: Path):
    result = _run(
        tmp_path,
        [{"Vendor": "Acme Corp", "Amount": 60}, {"Vendor": "ACME CORPORATION", "Amount": 40}],
        [{"Vendor": "acme corporation", "Amount": 100}],
        key_file_1=["Vendor"], key_file_2=["Vendor"],
    )
    # Both source spellings are one vendor: combined to 100 and matched.
    assert result["summary"]["exact_matches"] == 1
    assert result["summary"]["field_discrepancies"] == 0
    assert result["universal_data"]["matched_records"][0]["GROUP CLASSIFICATION"] == "Many-to-One Match"


def test_genuinely_ambiguous_entities_are_never_auto_matched(tmp_path: Path):
    result = _run(
        tmp_path,
        [{"ID": "INV009", "Name": "ABC Ltd", "Amount": 10}],
        [{"ID": "INV009", "Name": "ABC Pvt Ltd", "Amount": 10}],
        key_file_1=["ID"], key_file_2=["ID"],
        secondary_conditions=[{"source_column": "Name", "destination_column": "Name", "comparison_method": "exact_text"}],
    )
    assert result["summary"]["exact_matches"] == 0
    assert "NAME differs" in _identity(result)[0]["MATCH EXPLANATION"]


def test_saved_aliases_extend_normalization_for_one_run_only(tmp_path: Path):
    options = dict(
        key_file_1=["ID"], key_file_2=["ID"],
        secondary_conditions=[{"source_column": "Name", "destination_column": "Name", "comparison_method": "exact_text"}],
    )
    source, destination = [{"ID": "1", "Name": "IBM", "Amount": 5}], [{"ID": "1", "Name": "International Business Machines", "Amount": 5}]
    assert _run(tmp_path, source, destination, **options)["summary"]["exact_matches"] == 0
    with_alias = _run(tmp_path, source, destination, saved_aliases=[("IBM", "International Business Machines")], **options)
    assert with_alias["summary"]["exact_matches"] == 1
    assert "Alias: IBM" in _identity(with_alias)[0]["NORMALIZATION APPLIED"]
    assert _run(tmp_path, source, destination, **options)["summary"]["exact_matches"] == 0  # Not leaked.


# ── Transformations ────────────────────────────────────────────────────────
def test_debit_credit_to_signed_amount_and_sign_inversion(tmp_path: Path):
    result = _run(
        tmp_path,
        [{"Ref": "A1", "Debit": 150, "Credit": None}, {"Ref": "A2", "Debit": None, "Credit": 40}],
        [{"Ref": "A1", "Amount": -150}, {"Ref": "A2", "Amount": 40}],
        key_file_1=["Ref"], key_file_2=["Ref"],
        rules=[{"file_1_fields": ["Signed Amount"], "file_2_fields": ["Amount"]}],
        transformations=[
            {"operation": "debit_credit_to_signed", "side": "source", "params": {"debit_column": "Debit", "credit_column": "Credit"}, "output_column": "Signed Amount"},
            {"operation": "invert_sign", "side": "destination", "columns": ["Amount"]},
        ],
    )
    assert result["summary"]["field_discrepancies"] == 0 and result["summary"]["exact_matches"] == 2
    matched = result["universal_data"]["matched_records"][0]
    assert matched["AMOUNT (FILE 2) (ORIGINAL)"] == -150  # Original kept for the report.
    assert any("Debit/Credit" in step for step in result["universal_data"]["metadata"]["transformations"])


def test_code_cleanup_transformations_apply_before_matching(tmp_path: Path):
    result = _run(
        tmp_path,
        [{"Code": "  VND-00042 ", "Amount": 1}],
        [{"Code": "00042", "Amount": 1}],
        key_file_1=["Code"], key_file_2=["Code"],
        transformations=[
            {"operation": "trim", "side": "source", "columns": ["Code"]},
            {"operation": "remove_prefix", "side": "source", "columns": ["Code"], "params": {"text": "VND-"}},
        ],
    )
    assert result["summary"]["exact_matches"] == 1
    assert result["universal_data"]["matched_records"][0]["MATCH KEY"] == "00042"


def test_unknown_or_incomplete_transformations_are_rejected():
    from app.schemas.reconciliation import TransformationStep

    with pytest.raises(ValueError):
        TransformationStep(operation="eval", columns=["A"])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        TransformationStep(operation="replace_text", columns=["A"])
    with pytest.raises(ValueError):
        TransformationStep(operation="debit_credit_to_signed", params={"debit_column": "D", "credit_column": "C"})


# ── Keyless passes ─────────────────────────────────────────────────────────
_AMOUNT_DATE_PASS = {
    "type": "amount_date", "amount_source": "Amount", "amount_destination": "Amount",
    "date_source": "Date", "date_destination": "Date", "date_window_days": 3,
}


def test_keyless_pass_matches_only_records_left_by_the_key_pass(tmp_path: Path):
    result = _run(
        tmp_path,
        [
            {"Ref": "R1", "Date": "01/09/2026", "Amount": 500},
            {"Ref": "R2", "Date": "05/09/2026", "Amount": 750},  # Ref missing in bank.
        ],
        [
            {"Ref": "R1", "Date": "01/09/2026", "Amount": 500},
            {"Ref": "BANK-77", "Date": "07/09/2026", "Amount": 750},
        ],
        key_file_1=["Ref"], key_file_2=["Ref"],
        matching_passes=[_AMOUNT_DATE_PASS],
    )
    identities = _identity(result)
    assert identities[0]["MATCH PASS"] == "Primary key"
    assert identities[1]["MATCH PASS"] == "Pass 2: Amount + date ±3 days"
    assert identities[1]["IDENTITY CLASSIFICATION"] == "EXCEPTION_MATCH"  # Dates differ: confirm.
    assert "2 days apart" in identities[1]["MATCH EXPLANATION"]
    assert result["summary"]["keyless_matches"] == 1
    assert result["summary"]["only_in_file_1"] == 0 and result["summary"]["only_in_file_2"] == 0


def test_keyless_pass_never_breaks_a_tie(tmp_path: Path):
    result = _run(
        tmp_path,
        [{"Date": "01/09/2026", "Amount": 100}, {"Date": "02/09/2026", "Amount": 100}],
        [{"Date": "01/09/2026", "Amount": 100}],
        key_file_1=[], key_file_2=[], matching_passes=[_AMOUNT_DATE_PASS],
    )
    # Both source rows qualify for the one bank row: nobody is matched.
    assert result["summary"]["matched_records"] == 0
    assert {item["IDENTITY CLASSIFICATION"] for item in _identity(result)} == {"AMBIGUOUS_MATCH"}


def test_keyless_only_rule_with_exact_amount_and_date_is_an_exact_match(tmp_path: Path):
    result = _run(
        tmp_path,
        [{"Date": "03/09/2026", "Amount": 42.5, "Narration": "NEFT ACME CORP"}],
        [{"Date": "2026-09-03", "Amount": 42.50, "Narration": "Acme Corporation NEFT"}],
        key_file_1=[], key_file_2=[],
        matching_passes=[{**_AMOUNT_DATE_PASS, "date_window_days": 0, "narrative_source": "Narration", "narrative_destination": "Narration"}],
    )
    identity = _identity(result)[0]
    assert identity["IDENTITY CLASSIFICATION"] == "EXACT_MATCH"
    assert "reference 100% similar" in identity["MATCH EXPLANATION"]


def test_amount_tolerance_pass_respects_secondary_keys(tmp_path: Path):
    tolerance_pass = {"type": "amount_tolerance", "amount_source": "Amount", "amount_destination": "Amount", "amount_tolerance": 1}
    conditions = [{"source_column": "Vendor", "destination_column": "Vendor", "comparison_method": "exact_text"}]
    result = _run(
        tmp_path,
        [{"Inv": "X1", "Vendor": "Acme", "Amount": 100.4}],
        [{"Inv": "Y9", "Vendor": "Other Co", "Amount": 100}, {"Inv": "Y8", "Vendor": "ACME", "Amount": 99.8}],
        key_file_1=["Inv"], key_file_2=["Inv"], secondary_conditions=conditions, matching_passes=[tolerance_pass],
    )
    matched = result["universal_data"]["exceptions"] or result["universal_data"]["matched_records"]
    assert result["summary"]["keyless_matches"] == 1
    identity = _identity(result)[0]
    assert identity["CANDIDATE KEY"].startswith("Y8")  # Same vendor, not the closer amount.
    assert matched


def test_rule_without_keys_or_passes_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="primary-key column"):
        _run(tmp_path, [{"A": 1, "Amount": 1}], [{"A": 1, "Amount": 1}], key_file_1=[], key_file_2=[])


def test_month_first_date_convention_changes_how_ambiguous_dates_are_read(tmp_path: Path):
    source = [{"Ref": "R1", "Date": "03/04/2026", "Amount": 10}]
    destination = [{"Ref": "R1", "Date": "2026-03-04", "Amount": 10}]
    options = dict(key_file_1=["Ref"], key_file_2=["Ref"], rules=[{"file_1_fields": ["Date"], "file_2_fields": ["Date"]}])
    day_first = _run(tmp_path, source, destination, **options)
    month_first = _run(tmp_path, source, destination, date_dayfirst=False, **options)
    assert day_first["summary"]["field_discrepancies"] == 1  # 3 April vs 4 March.
    assert month_first["summary"]["field_discrepancies"] == 0
