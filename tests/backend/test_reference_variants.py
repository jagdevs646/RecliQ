"""Reference-number variants and named match levels (docs/adr/0002)."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

import pandas as pd
import pytest
from pydantic import ValidationError

from app.reconciliation_engine.engine import run_generic_reconciliation
from app.reconciliation_engine.match_levels import AMOUNT_LEVELS, DATE_LEVELS, TEXT_LEVELS
from app.reconciliation_engine.matching.reference_variants import VARIANT_RULES, key_forms, years_compatible
from app.reconciliation_engine.preprocessing import prepare_dataframe
from app.reconciliation_engine.tolerance import Band
from app.schemas.reconciliation import SimilarityPolicy

AMOUNT_RULE = [{"file_1_fields": ["Amount"], "file_2_fields": ["Amount"]}]


def _rule_for(left: str, right: str) -> str | None:
    for rule in VARIANT_RULES:
        form_1, form_2 = key_forms(rule, [left], [True], [None]), key_forms(rule, [right], [True], [None])
        if form_1 and form_2 and form_1[0] == form_2[0] and (form_1[1] or form_2[1]) and years_compatible(form_1[2], form_2[2]):
            return rule.name
    return None


@pytest.mark.parametrize("left, right, expected", [
    # The same invoice written differently: paired by a named rule.
    ("INV-0105", "INV-105", "leading_zeros"),
    ("INV-2026-0005", "INV-2026-5", "leading_zeros"),
    ("INV-1O5", "INV-105", "look_alike"),
    ("GST/0105", "0105", "prefix_year"),
    ("INV/2026-27/105", "105", "prefix_year"),
    ("INV/2026/0105", "105", "prefix_year"),
    ("FY26-27/105", "INV-105", "prefix_year"),
    # Different invoices: no rule pairs them, whatever their fuzzy score (86-93%).
    ("INV-1001", "INV-1002", None),
    ("INV-2026-0451", "INV-2026-0457", None),
    ("INV-10", "INV-100", None),
    ("TXN202609150001", "TXN202609150002", None),
    ("INV-1054", "INV-1045", None),  # A typing slip is only ever a suggestion.
    # A series code or a different year is a different document.
    ("A-105", "B-105", None),
    ("A-105", "105", None),
    ("2025-0451", "2026/0451", None),
    ("CN-105", "105", None),  # Credit note vs invoice.
])
def test_variant_rules_pair_formats_never_different_invoices(left, right, expected):
    assert _rule_for(left, right) == expected
    assert _rule_for(right, left) == expected


def _reconcile(source: dict, destination: dict, level: str | None, mode: str = "rows", **kwargs) -> dict:
    policy = {"reference_matching": level} if level else {}
    return run_generic_reconciliation(
        prepare_dataframe(pd.DataFrame(source)),
        prepare_dataframe(pd.DataFrame(destination)),
        Path("unused.xlsx"),
        kwargs.pop("key_1", ["Invoice No"]),
        kwargs.pop("key_2", ["Invoice No"]),
        kwargs.pop("rules", AMOUNT_RULE),
        write_report=False,
        similarity_policy=policy,
        engine_mode=mode,
        **kwargs,
    )


def _decisions(result: dict) -> dict:
    return {
        record["MATCH KEY"]: (record["IDENTITY CLASSIFICATION"], record.get("CANDIDATE KEY") or None)
        for record in result["universal_data"]["identity_resolution"]
    }


BOOKS = {
    "Invoice No": ["INV-0105", "GST/0201", "INV/2026-27/305", "INV-1O7", "INV-1054", "INV-1001", "A-500", "INV-999"],
    "Amount": [100, 200, 300, 400, 500, 600, 700, 999],
}
PORTAL = {
    "Invoice No": ["INV-105", "0201", "305", "INV-107", "INV-1045", "INV-1002", "B-500", "INV-999"],
    "Amount": [100, 200, 300, 400, 500, 600, 700, 999],
}


@pytest.mark.parametrize("mode", ["rows", "columns"])
def test_formatting_level_is_the_default_and_keeps_todays_behaviour(mode):
    for level in (None, "formatting"):
        decisions = _decisions(_reconcile(BOOKS, PORTAL, level, mode))
        assert decisions["INV-999"] == ("EXACT_MATCH", "INV-999")
        assert all(classification == "NOT_FOUND" for key, (classification, _) in decisions.items() if key != "INV-999")


@pytest.mark.parametrize("mode", ["rows", "columns"])
def test_variants_level_matches_leading_zeros_and_reviews_other_variants(mode):
    result = _reconcile(BOOKS, PORTAL, "variants", mode)
    decisions = _decisions(result)

    assert decisions["INV-0105"] == ("EXACT_MATCH", "INV-105")  # Counted as matched.
    assert decisions["GST/0201"] == ("EXCEPTION_MATCH", "0201")
    assert decisions["INV/2026-27/305"] == ("EXCEPTION_MATCH", "305")
    assert decisions["INV-1O7"] == ("EXCEPTION_MATCH", "INV-107")
    for key in ("INV-1054", "INV-1001", "A-500"):
        assert decisions[key][0] == "NOT_FOUND"
    methods = {record["MATCH KEY"]: record.get("MATCH METHOD") for record in result["universal_data"]["identity_resolution"]}
    assert methods["INV-0105"] == "Key format differs (leading zeros)"
    assert methods["GST/0201"] == "Key variant (prefix or year)"


@pytest.mark.parametrize("mode", ["rows", "columns"])
def test_suggestions_are_review_only_and_need_the_amount_to_agree(mode):
    destination = {**PORTAL, "Amount": [100, 200, 300, 400, 500, 650, 700, 999]}  # INV-1002 has another amount.
    decisions = _decisions(_reconcile(BOOKS, destination, "suggestions", mode))

    assert decisions["INV-1054"] == ("EXCEPTION_MATCH", "INV-1045")  # Typing slip: proposed for review.
    assert decisions["INV-1001"][0] == "NOT_FOUND"  # Similar number, different amount: never proposed.
    assert decisions["A-500"][0] == "NOT_FOUND"
    assert decisions["INV-0105"] == ("EXACT_MATCH", "INV-105")


def test_neighbouring_invoices_are_never_paired_automatically_at_any_level():
    source = {"Invoice No": ["INV-1001", "INV-2026-0451"], "Amount": [500, 700]}
    destination = {"Invoice No": ["INV-1002", "INV-2026-0457"], "Amount": [500, 700]}
    for level in ("exact", "formatting", "variants", "suggestions"):
        for classification, _ in _decisions(_reconcile(source, destination, level)).values():
            assert classification != "EXACT_MATCH"


def test_variants_still_require_every_must_also_match_column():
    source = {"Invoice No": ["INV-0105"], "Vendor": ["Vendor A"], "Amount": [100]}
    destination = {"Invoice No": ["INV-105"], "Vendor": ["Vendor B"], "Amount": [100]}
    result = _reconcile(
        source, destination, "variants",
        secondary_conditions=[{"source_column": "Vendor", "destination_column": "Vendor", "comparison_method": "exact_text"}],
    )
    assert _decisions(result)["INV-0105"][0] == "NOT_FOUND"


def test_two_records_sharing_a_variant_are_ambiguous_not_guessed():
    source = {"Invoice No": ["INV-105"], "Amount": [100]}
    destination = {"Invoice No": ["INV-0105", "INV-00105"], "Amount": [100, 100]}
    result = _reconcile(source, destination, "variants")
    classification, _ = _decisions(result)["INV-105"]
    assert classification == "AMBIGUOUS_MATCH"
    assert result["exact_matches"] == 0


def test_a_suggestion_needs_a_clear_lead_over_the_next_candidate():
    # INV-1054 is equally close to INV-1045 and INV-1504 (same amount): no proposal.
    source = {"Invoice No": ["INV-1054"], "Amount": [500]}
    destination = {"Invoice No": ["INV-1045", "INV-1504"], "Amount": [500, 500]}
    assert _decisions(_reconcile(source, destination, "suggestions"))["INV-1054"][0] == "NOT_FOUND"


def test_exact_level_ignores_only_case_and_spaces():
    source = {"Invoice No": ["inv 005", "INV-006"], "Amount": [1, 2]}
    destination = {"Invoice No": ["INV  005", "INV/006"], "Amount": [1, 2]}
    exact = _decisions(_reconcile(source, destination, "exact"))
    assert exact["inv 005"][0] == "EXACT_MATCH"
    assert exact["INV-006"][0] == "NOT_FOUND"  # Punctuation differs.
    formatting = _decisions(_reconcile(source, destination, "formatting"))
    assert formatting["INV-006"][0] == "EXACT_MATCH"


def test_variant_pairings_explain_the_rule_in_the_report_reason():
    from app.reconciliation_engine.explanations import match_explanation

    result = _reconcile(BOOKS, PORTAL, "variants")
    reasons = {record["MATCH KEY"]: match_explanation(record, result["universal_data"]) for record in result["universal_data"]["identity_resolution"]}
    assert reasons["INV-0105"] == "Key matched after removing leading zeros (INV-0105 = INV-105)."
    assert reasons["GST/0201"].endswith("(GST/0201 ↔ 0201). Please confirm.")


def test_similarity_policy_accepts_only_known_reference_levels():
    assert SimilarityPolicy().reference_matching is None  # Saved plans: today's behaviour.
    assert SimilarityPolicy(reference_matching="variants").reference_matching == "variants"
    with pytest.raises(ValidationError):
        SimilarityPolicy(reference_matching="fuzzy")


def test_tolerance_descriptions_name_the_level():
    assert Band(field="AMOUNT", amount=0.05).describe() == "AMOUNT: ±0.05 (Rounding)"
    assert Band(field="AMOUNT", percent=1).describe() == "AMOUNT: ±1%"  # The name is the limit itself.
    assert Band(field="AMOUNT", amount=2.5).describe() == "AMOUNT: ±2.5"  # Custom value.
    assert Band(field="NARRATION", similarity=80).describe() == "NARRATION: text at least 80% similar (Close)"


def test_match_levels_match_the_screen():
    """The backend names and the screen's level picker use the same values."""
    source = (ROOT.parent / "frontend" / "src" / "lib" / "matchLevels.ts").read_text(encoding="utf-8")

    def table(name: str) -> list[tuple]:
        body = re.search(rf"export const {name}[^=]*= \[(.*?)\];", source, re.S).group(1)
        return [tuple(item) for item in re.findall(r'id: "(\w+)", label: "([^"]+)"', body)]

    assert table("TEXT_LEVELS") == [(level_id, label) for level_id, label, _ in TEXT_LEVELS]
    assert table("AMOUNT_LEVELS") == [(level_id, label) for level_id, label, *_ in AMOUNT_LEVELS]
    assert table("DATE_LEVELS") == [(level_id, label) for level_id, label, _ in DATE_LEVELS]


def test_data_check_counts_variant_pairings_instead_of_warning():
    from app.services.precheck_service import _check_rule

    source = prepare_dataframe(pd.DataFrame({"Invoice No": ["INV-0105", "INV-0106", "GST/0107", "INV-108"], "Amount": [1, 2, 3, 4]}))
    destination = prepare_dataframe(pd.DataFrame({"Invoice No": ["INV-105", "INV-106", "0107", "INV-108"], "Amount": [1, 2, 3, 4]}))
    rule = {"key_file_1": ["Invoice No"], "key_file_2": ["Invoice No"], "rules": AMOUNT_RULE}

    def overlap(level: str) -> dict:
        issues = _check_rule(source, destination, {**rule, "similarity_policy": {"reference_matching": level}})
        return next(issue for issue in issues if issue["category"] == "key_overlap")

    formatting = overlap("formatting")
    assert formatting["severity"] == "warning" and formatting["count"] == 1  # Only INV-108 exactly.
    variants = overlap("variants")
    assert variants["severity"] == "info" and variants["count"] == 1
    assert "2 more pair after removing leading zeros" in variants["message"]
    assert "1 more differ only by a prefix, year or look-alike character" in variants["message"]
