from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from app.reconciliation_engine.cache import (
    is_blank,
    normalize_header,
    normalize_text,
    parse_date_value,
    to_number,
    use_date_convention,
)
from app.reconciliation_engine.matching import (
    IndexedCandidateMatcher,
    MatchClassification,
    MatchResult,
    compare_values,
    detect_matcher_type,
    match_threshold,
    non_empty_values,
)
from app.reconciliation_engine.matching.indexed_matcher import normalized_identity_value
from app.reconciliation_engine.matching.pass_matcher import pass_label, run_pass, validate_pass
from app.reconciliation_engine.preprocessing import (
    aggregate_by_key,
    fields_label,
    merge_duplicate_invoices,
    normalise_gst_df,
    normalize_fields,
    prepare_dataframe,
    read_excel_columns,
)
from app.reconciliation_engine.normalization import build_normalizer, normalize_dataframe, use_normalizer
from app.reconciliation_engine.normalization.entities import active_normalizer
from app.reconciliation_engine.tolerance import apply_tolerance_bands, prepare_bands
from app.reconciliation_engine.transformations import apply_transformations, original_values
from app.reconciliation_engine.learning import LearningContext
from app.reconciliation_engine.resolution import Evaluator, describe_rule, referenced_values, resolution_fields
from app.reconciliation_engine.matching.advanced_matcher import consolidate_duplicate_keys
from app.reconciliation_engine.progress_tracker import ProgressTracker
from app.reconciliation_engine.report_generator import (
    write_generic_report,
    write_gst_output,
)
from app.reconciliation_engine.utilities import (
    collect_rule_value,
    is_numeric_column,
    validate_columns,
    validate_combined_numeric_rules,
)
from app.reconciliation_engine.universal_mapper import build_universal_data_model
from app.reconciliation_engine.universal_reporter import generate_enterprise_report

REQUIRED_GST_COLUMNS = [
    "GSTR",
    "NAME OF TRADER/FIRM/COMPANY",
    "INVOICE NO.",
    "INVOICE DATE",
    "TAXABLE VALUE",
    "IGST",
    "CGST",
    "SGST",
    "CESS",
    "INVOICE VALUE",
]

GST_AMOUNT_COLUMNS = ["TAXABLE VALUE", "IGST", "CGST", "SGST", "CESS", "INVOICE VALUE"]
GST_MERGE_KEY_COLUMNS = ["GSTR", "NAME OF TRADER/FIRM/COMPANY", "INVOICE NO.", "INVOICE DATE"]
GST_TEXT_REVIEW_THRESHOLD = 85


def validate_gst_columns(df: pd.DataFrame) -> list[str]:
    return [col for col in REQUIRED_GST_COLUMNS if col not in df.columns]


# Column-name types that are never amounts, even when the cell holds a number
# ("Invoice No" 0 vs blank is a missing identifier, not a zero amount).
_NON_AMOUNT_NAME_TYPES = {"date", "invoice", "gstin", "pan", "identifier", "company_name", "person_name"}


def _blank_as_zero(
    file_1_value: object,
    file_2_value: object,
    file_1_fields: list[str],
    file_2_fields: list[str],
) -> tuple[object, object]:
    """For a numeric field, a blank on one side counts as 0 when the other side
    is a number, so 0 vs blank is equal and 5 vs blank shows a difference of 5.

    Applies only to this comparison: the displayed values stay as they were,
    both-blank stays "both blank", and text or date fields are untouched.
    """
    blank_1, blank_2 = is_blank(file_1_value), is_blank(file_2_value)
    if blank_1 == blank_2:
        return file_1_value, file_2_value
    populated = file_2_value if blank_1 else file_1_value
    if to_number(populated) is None:
        return file_1_value, file_2_value
    name_type = detect_matcher_type(None, fields_label(file_1_fields), fields_label(file_2_fields))
    if name_type in _NON_AMOUNT_NAME_TYPES:
        return file_1_value, file_2_value
    return (0 if blank_1 else file_1_value), (0 if blank_2 else file_2_value)


def compare_rule_values(
    file_1_row: dict,
    file_2_row: dict,
    file_1_fields: list[str],
    file_2_fields: list[str],
    rules_sink: list[str] | None = None,
) -> dict:
    file_1_value, type_hint_1 = collect_rule_value(file_1_row, file_1_fields)
    file_2_value, type_hint_2 = collect_rule_value(file_2_row, file_2_fields)
    label = fields_label(file_1_fields)
    # A blank side has no type of its own; the populated side decides.
    matcher_type = type_hint_1 or type_hint_2

    if matcher_type is None:
        matcher_type = detect_matcher_type(
            [file_1_value, file_2_value],
            fields_label(file_1_fields),
            fields_label(file_2_fields),
        )

    compared_1, compared_2 = file_1_value, file_2_value
    if matcher_type == "numeric":
        compared_1, compared_2 = _blank_as_zero(file_1_value, file_2_value, file_1_fields, file_2_fields)

    result = compare_values(
        compared_1,
        compared_2,
        fields_label(file_1_fields),
        fields_label(file_2_fields),
        matcher_type,
    )

    if rules_sink is not None and result.rules:
        rules_sink.extend(result.rules)
    if result.matched and result.confidence == 100:
        return {}

    if result.matcher_type == "numeric":
        value_1 = to_number(file_1_value) or 0.0
        value_2 = to_number(file_2_value) or 0.0
        return {
            f"{label} (FILE 1)": file_1_value,
            f"{label} (FILE 2)": file_2_value,
            f"{label} DIFF": round(value_1 - value_2, 2),
            f"{label} STATUS": result.status if result.matched else "Mismatch",
        }

    status = result.status if result.matched else "Mismatch"
    return {
        f"{label} (FILE 1)": file_1_value,
        f"{label} (FILE 2)": file_2_value,
        f"{label} CONFIDENCE": f"{result.confidence}%",
        f"{label} STATUS": status,
    }


def _normalize_secondary_conditions(conditions: list[dict] | None) -> list[dict]:
    """Translate API column names once, before the indexed matcher sees them."""
    normalized: list[dict] = []
    for condition in conditions or []:
        if not isinstance(condition, dict):
            continue
        source_column = normalize_header(condition.get("source_column", ""))
        destination_column = normalize_header(condition.get("destination_column", ""))
        if not source_column or not destination_column:
            raise ValueError("Secondary conditions require a source and destination column.")
        normalized.append(
            {
                **condition,
                "source_column": source_column,
                "destination_column": destination_column,
            }
        )
    return normalized


# Up to this many secondary-key candidates are scored directly on primary-key
# similarity; larger sets are pre-filtered through the primary-key token index.
_MAX_DIRECT_SIMILARITY_CANDIDATES = 25


def _secondary_group_value(method: str):
    """Deterministic normal form of a secondary key, or None if not groupable."""
    if method == "exact_text":
        normalizer = active_normalizer()
        return lambda value: None if is_blank(value) else normalizer.identity(value)
    if method == "normalized_date":
        return lambda value: parsed.isoformat() if (parsed := parse_date_value(value)) else None
    return None  # Tolerance and fuzzy conditions do not define an identity.


def _add_secondary_group_keys(file_1_df: pd.DataFrame, file_2_df: pd.DataFrame, conditions: list[dict]) -> tuple[list[str], list[str]]:
    """Add normalized secondary-key columns used to consolidate duplicate rows."""
    group_keys: list[str] = []
    for index, condition in enumerate(conditions):
        normalize = _secondary_group_value(str(condition.get("comparison_method", "")))
        if normalize is None:
            continue
        column = f"__SECONDARY_KEY_{index}__"
        file_1_df[column] = file_1_df[condition["source_column"]].map(normalize)
        file_2_df[column] = file_2_df[condition["destination_column"]].map(normalize)
        group_keys.append(column)
    return group_keys, list(group_keys)


def _add_key_identity_columns(df: pd.DataFrame, match_keys: list[str], matcher_types: list[str]) -> list[str]:
    """Add each key's exact-match identity (the form the index uses), so rows
    are consolidated exactly when the matcher would treat them as one key."""
    columns = []
    for index, (column, matcher_type) in enumerate(zip(match_keys, matcher_types)):
        identity_column = f"__KEY_{index}__"
        df[identity_column] = df[column].map(lambda value, kind=matcher_type: normalized_identity_value(value, kind))
        columns.append(identity_column)
    return columns


def _numeric_rule_columns(df: pd.DataFrame, field_lists: list[list[str]]) -> list[str]:
    """Mapped columns whose values are all numeric: these are summed when grouped."""
    fields = dict.fromkeys(field for fields in field_lists for field in fields)
    return [field for field in fields if field in df.columns and is_numeric_column(df[field])]


def _secondary_mismatches(file_1_row: dict, file_2_row: dict, conditions: list[dict]) -> list[str]:
    """Describe which secondary keys differ for a row whose primary key matched."""
    return [
        f"{conditions[index]['source_column']} differs "
        f"('{file_1_row.get(conditions[index]['source_column'])}' vs '{file_2_row.get(conditions[index]['destination_column'])}')"
        for index in _failed_secondary_conditions(file_1_row, file_2_row, conditions)
    ]


def _failed_secondary_conditions(file_1_row: dict, file_2_row: dict, conditions: list[dict]) -> list[int]:
    """Positions of the secondary keys that differ for a pair of rows."""
    failed = []
    for index, condition in enumerate(conditions):
        source_value = file_1_row.get(condition["source_column"])
        destination_value = file_2_row.get(condition["destination_column"])
        method = condition.get("comparison_method")
        if method == "exact_text":
            # Exact after deterministic normalization ("Pvt" == "Private").
            passed = active_normalizer().equivalent(source_value, destination_value)
        elif method == "normalized_date":
            passed = parse_date_value(source_value) is not None and parse_date_value(source_value) == parse_date_value(destination_value)
        elif method == "numeric_tolerance":
            left, right = to_number(source_value), to_number(destination_value)
            passed = left is not None and right is not None and abs(left - right) <= float(condition.get("numeric_tolerance") or 0)
        else:
            passed = compare_values(source_value, destination_value, condition["source_column"], condition["destination_column"]).matched
        if not passed:
            failed.append(index)
    return failed


def _conditions_replaced_by_pass(config: dict, conditions: list[dict]) -> set[int]:
    """Secondary keys a pass's own date window replaces: an exact-date key on
    the same two date columns (the pass checks those dates within ±N days)."""
    return {
        index
        for index, condition in enumerate(conditions)
        if condition.get("comparison_method") == "normalized_date"
        and config.get("date_source")
        and condition["source_column"] == config["date_source"]
        and condition["destination_column"] == config.get("date_destination")
    }


def _primary_similarity_result(
    file_1_row: dict,
    file_2_row: dict,
    file_1_key_columns: list[str],
    file_2_key_columns: list[str],
    detected_matcher_types: list[str],
    similarity_policy: dict | None,
) -> tuple[MatchResult, int]:
    """Compare raw primary keys only after secondary filters isolate one row."""
    policy = similarity_policy or {}
    override = policy.get("matcher_type_override")
    component_results: list[MatchResult] = []
    for source_column, destination_column, detected_type in zip(
        file_1_key_columns,
        file_2_key_columns,
        detected_matcher_types,
    ):
        component_results.append(
            compare_values(
                file_1_row.get(source_column),
                file_2_row.get(destination_column),
                source_column,
                destination_column,
                override or detected_type,
            )
        )

    if not component_results:
        return MatchResult(False, 0, "text", "No primary-key components"), 100

    threshold = policy.get("threshold")
    if threshold is None:
        threshold = max(match_threshold(result.matcher_type) for result in component_results)
    threshold = max(0, min(100, int(threshold)))

    if len(component_results) == 1:
        return component_results[0], threshold

    confidence = min(result.confidence for result in component_results)
    matched = all(result.confidence >= threshold for result in component_results)
    detail = "; ".join(
        f"{source_column}: {result.status} ({result.confidence}%)"
        for source_column, result in zip(file_1_key_columns, component_results)
    )
    return (
        MatchResult(
            matched,
            confidence,
            "composite",
            "Composite primary-key similarity",
            detail,
            " | ".join(result.value1_normalized for result in component_results),
            " | ".join(result.value2_normalized for result in component_results),
        ),
        threshold,
    )


_TEXT_LIKE_TYPES = {"text", "company_name", "person_name"}


def _identity_explanation(
    classification: MatchClassification,
    result: MatchResult | None = None,
    threshold: int | None = None,
    secondary_names: str | None = None,
    secondary_mismatch: list[str] | None = None,
    normalization: list[str] | None = None,
) -> str:
    """Plain-language reason shown in the report and the dashboard.

    ``secondary_names`` lists the configured secondary keys, or is None when
    none are configured. ``normalization`` names the rules (e.g. "Pvt →
    Private") that made the key or secondary values equal.
    """
    if secondary_mismatch:
        return f"The key exists in the other file, but {'; '.join(secondary_mismatch)}."
    if classification is MatchClassification.EXACT_MATCH:
        base = "Key and secondary keys matched" if secondary_names else "Key matched"
        return f"{base} after normalization ({', '.join(normalization)})." if normalization else f"{base}."
    if classification is MatchClassification.AMBIGUOUS_MATCH:
        if secondary_names is None:
            return "Several records in the other file have this key, so none was chosen."
        return f"Several records in the other file have a similar key and the same {secondary_names}, so none was chosen."
    if classification is MatchClassification.NOT_FOUND and result is None:
        if secondary_names is None:
            return "No record in the other file has this key."
        return f"No record in the other file has this key (or a close variant) with the same {secondary_names}."
    assert result is not None
    closeness = f"{result.confidence}% similar, {threshold}% required"
    if classification is MatchClassification.EXCEPTION_MATCH:
        return f"Key differs slightly ({result.status.lower()}, {closeness}), but every secondary key matched. Please confirm."
    return f"The closest record with the same {secondary_names or 'secondary keys'} has a different key ({closeness})."


def _prepare_passes(matching_passes: list[dict] | None) -> list[dict]:
    """Validate keyless passes and translate their column names."""
    prepared = []
    for configured in matching_passes or []:
        if not isinstance(configured, dict) or configured.get("enabled", True) is False:
            continue
        config = dict(configured)
        for field in ("amount_source", "amount_destination", "date_source", "date_destination", "narrative_source", "narrative_destination"):
            if config.get(field):
                config[field] = normalize_header(config[field])
        validate_pass(config)
        prepared.append(config)
    return prepared


def run_generic_reconciliation(
    file_1_df: pd.DataFrame,
    file_2_df: pd.DataFrame,
    output_path: Path,
    key_file_1: list[str],
    key_file_2: list[str],
    rules: list[dict],
    orientation: str = "vertical",
    include_columns_file_1: list[str] | None = None,
    include_columns_file_2: list[str] | None = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    file_1_name: str = "File 1",
    file_2_name: str = "File 2",
    is_cancelled: Optional[Callable[[], bool]] = None,
    write_report: bool = True,
    secondary_conditions: list[dict] | None = None,
    similarity_policy: dict | None = None,
    date_only_override: bool = False,
    transformations: list[dict] | None = None,
    matching_passes: list[dict] | None = None,
    normalization: dict | None = None,
    date_dayfirst: bool = True,
    saved_aliases: list[tuple[str, str]] | None = None,
    resolution_rules: list[dict] | None = None,
    learning: LearningContext | None = None,
    tolerances: list[dict] | None = None,
) -> dict:
    """Reconcile one sheet rule.

    ``normalization`` configures business-name normalization and aliases,
    ``date_dayfirst`` the convention for ambiguous dates such as 03/04/2026,
    ``transformations`` the pre-match value transformations and
    ``matching_passes`` the ordered keyless passes that run after the key pass.
    ``learning`` carries reviewers' earlier decisions and ``resolution_rules``
    the organization's auto-resolution rules for recurring exceptions.
    ``tolerances`` are the differences to accept after matching (see
    ``tolerance.py``).
    """
    normalizer = build_normalizer(normalization, saved_aliases or ())
    evaluator = Evaluator.from_config(resolution_rules, learning)
    with use_date_convention(date_dayfirst), use_normalizer(normalizer):
        return _run_generic_reconciliation(
            file_1_df, file_2_df, output_path, key_file_1, key_file_2, rules,
            include_columns_file_1, include_columns_file_2, progress_callback, file_1_name, file_2_name,
            is_cancelled, write_report, secondary_conditions, similarity_policy, date_only_override,
            transformations, matching_passes, normalizer, date_dayfirst, evaluator, learning, tolerances,
        )


def _run_generic_reconciliation(
    file_1_df: pd.DataFrame,
    file_2_df: pd.DataFrame,
    output_path: Path,
    key_file_1: list[str] | None,
    key_file_2: list[str] | None,
    rules: list[dict],
    include_columns_file_1: list[str] | None,
    include_columns_file_2: list[str] | None,
    progress_callback: Optional[Callable[[int, str], None]],
    file_1_name: str,
    file_2_name: str,
    is_cancelled: Optional[Callable[[], bool]],
    write_report: bool,
    secondary_conditions: list[dict] | None,
    similarity_policy: dict | None,
    date_only_override: bool,
    transformations: list[dict] | None,
    matching_passes: list[dict] | None,
    normalizer,
    date_dayfirst: bool,
    evaluator: Evaluator | None = None,
    learning: LearningContext | None = None,
    tolerances: list[dict] | None = None,
) -> dict:
    tracker = ProgressTracker(progress_callback)
    tracker.reading_excel()

    # Preprocess incoming DFs to ensure row numbers and basic normalization
    if "_ROW_NO" not in file_1_df.columns:
        file_1_df["_ROW_NO"] = file_1_df.index + 2
    if "_ROW_NO" not in file_2_df.columns:
        file_2_df["_ROW_NO"] = file_2_df.index + 2

    # Pre-match transformations run on working copies; originals are kept.
    transformed_1 = apply_transformations(file_1_df, transformations, "source")
    transformed_2 = apply_transformations(file_2_df, transformations, "destination")
    file_1_df, file_2_df = transformed_1.df, transformed_2.df

    file_1_id_col = [normalize_header(k) for k in key_file_1 or []]
    file_2_id_col = [normalize_header(k) for k in key_file_2 or []]
    passes = _prepare_passes(matching_passes)
    if len(file_1_id_col) != len(file_2_id_col):
        raise ValueError("File 1 and File 2 must use the same number of primary-key columns.")
    if not file_1_id_col and not passes:
        raise ValueError("At least one primary-key column is required (or an amount/date matching pass).")
    has_keys = bool(file_1_id_col)

    normalized_rules = [
        (
            normalize_fields(rule.get("file_1_fields", [])),
            normalize_fields(rule.get("file_2_fields", [])),
        )
        for rule in rules
    ]
    normalized_rules = [(left, right) for left, right in normalized_rules if left and right]
    file_1_extra = normalize_fields(include_columns_file_1 or [])
    file_2_extra = normalize_fields(include_columns_file_2 or [])
    normalized_secondary_conditions = _normalize_secondary_conditions(secondary_conditions)

    if not normalized_rules:
        raise ValueError("At least one reconciliation rule is required.")
    compared_labels = [(fields_label(left), fields_label(right)) for left, right in normalized_rules]
    tolerance_bands = prepare_bands(tolerances, compared_labels)

    validate_columns(file_1_df, [*file_1_id_col, *file_1_extra], "File 1")
    validate_columns(file_2_df, [*file_2_id_col, *file_2_extra], "File 2")
    for condition in normalized_secondary_conditions:
        validate_columns(file_1_df, [condition["source_column"]], "File 1 secondary condition")
        validate_columns(file_2_df, [condition["destination_column"]], "File 2 secondary condition")
    for left, right in normalized_rules:
        validate_columns(file_1_df, left, "File 1 rule")
        validate_columns(file_2_df, right, "File 2 rule")
    for config in passes:
        validate_columns(file_1_df, [config[f] for f in ("amount_source", "date_source", "narrative_source") if config.get(f)], "File 1 matching pass")
        validate_columns(file_2_df, [config[f] for f in ("amount_destination", "date_destination", "narrative_destination") if config.get(f)], "File 2 matching pass")
    validate_combined_numeric_rules(file_1_df, file_2_df, normalized_rules)

    # Key types come from the raw values. Name/text keys keep their raw values
    # for matching, so business normalization can see word boundaries;
    # identifier keys are compacted (INV-005 == INV/005) as before.
    raw_key_types = [
        detect_matcher_type(
            non_empty_values(list(file_1_df[source].head(500)) + list(file_2_df[destination].head(500)), limit=200),
            source,
            destination,
        )
        for source, destination in zip(file_1_id_col, file_2_id_col)
    ]
    text_keys_1 = {column for column, kind in zip(file_1_id_col, raw_key_types) if kind in _TEXT_LIKE_TYPES}
    text_keys_2 = {column for column, kind in zip(file_2_id_col, raw_key_types) if kind in _TEXT_LIKE_TYPES}

    # 1. Normalize data
    date_cols = list(dict.fromkeys(
        [rule[0][0] for rule in normalized_rules if 'date' in rule[0][0].lower()]
        + [key for key in file_1_id_col if 'date' in key.lower()]
    ))
    text_cols = [rule[0][0] for rule in normalized_rules if 'name' in rule[0][0].lower() or 'vendor' in rule[0][0].lower()]
    num_cols = [rule[0][0] for rule in normalized_rules if 'amount' in rule[0][0].lower() or 'value' in rule[0][0].lower() or 'tax' in rule[0][0].lower()]

    f1_norm_config = {"date_columns": date_cols, "text_columns": text_cols, "number_columns": num_cols,
                      "id_columns": [key for key in file_1_id_col if key not in text_keys_1]}
    f2_date_cols = list(dict.fromkeys(
        [r[1][0] for r in normalized_rules if r[0][0] in date_cols]
        + [key for key in file_2_id_col if 'date' in key.lower()]
    ))
    f2_norm_config = {"date_columns": f2_date_cols,
                      "text_columns": [r[1][0] for r in normalized_rules if r[0][0] in text_cols],
                      "number_columns": [r[1][0] for r in normalized_rules if r[0][0] in num_cols],
                      "id_columns": [key for key in file_2_id_col if key not in text_keys_2]}

    file_1_df = normalize_dataframe(file_1_df, f1_norm_config)
    file_2_df = normalize_dataframe(file_2_df, f2_norm_config)

    # Use normalized keys for matching (raw values for name/text keys).
    file_1_match_keys = [
        k if k in text_keys_1 else (f"NORM_{k}" if f"NORM_{k}" in file_1_df.columns else k) for k in file_1_id_col
    ]
    file_2_match_keys = [
        k if k in text_keys_2 else (f"NORM_{k}" if f"NORM_{k}" in file_2_df.columns else k) for k in file_2_id_col
    ]

    key_matcher_types = [
        detect_matcher_type(
            list(file_1_df[source_key]) + list(file_2_df[destination_key]),
            source_column,
            destination_column,
        )
        for source_key, destination_key, source_column, destination_column in zip(
            file_1_match_keys,
            file_2_match_keys,
            file_1_id_col,
            file_2_id_col,
        )
    ]

    # Consolidate rows sharing the full identity (primary key + deterministic
    # secondary keys) on each side, so e.g. two INV-007 / Vendor A invoices of
    # 1500 reconcile as one 3000 record instead of being dropped as duplicates.
    # Identity uses the same normal form as matching ("ABC Pvt Ltd" and
    # "ABC Private Limited" are one vendor).
    if has_keys:
        identity_1 = _add_key_identity_columns(file_1_df, file_1_match_keys, key_matcher_types)
        identity_2 = _add_key_identity_columns(file_2_df, file_2_match_keys, key_matcher_types)
        group_keys_1, group_keys_2 = _add_secondary_group_keys(file_1_df, file_2_df, normalized_secondary_conditions)
        file_1_df = consolidate_duplicate_keys(
            file_1_df, identity_1 + group_keys_1, _numeric_rule_columns(file_1_df, [left for left, _ in normalized_rules])
        )
        file_2_df = consolidate_duplicate_keys(
            file_2_df, identity_2 + group_keys_2, _numeric_rule_columns(file_2_df, [right for _, right in normalized_rules])
        )

    if is_cancelled and is_cancelled():
        raise InterruptedError("Reconciliation cancelled by user")

    tracker.building_indexes()

    if has_keys and len(key_matcher_types) == 1 and key_matcher_types[0] == "date" and not date_only_override:
        raise ValueError(
            "Date-only matching can be ambiguous because multiple transactions may occur on the same date. "
            "Select at least one additional identifying column or explicitly enable the date-only override."
        )
    indexed_matcher = IndexedCandidateMatcher(file_2_df, file_2_match_keys, key_matcher_types) if has_keys else None
    is_composite_key = len(file_1_match_keys) > 1
    primary_key_1 = file_1_match_keys[0] if has_keys else None
    primary_key_2 = file_2_match_keys[0] if has_keys else None

    reconciliation_results: list[dict] = []
    file_1_not_found: list[dict] = []
    matched_records: list[dict] = []
    matched_file_2_indices: set = set()
    identity_resolution: list[dict] = []
    auto_resolved: list[dict] = []  # Exceptions explained by the organization's rules.
    field_discrepancy_count = 0
    normalization_counts: dict[str, int] = {}

    tracker.matching_records()

    secondary_names = ", ".join(condition["source_column"] for condition in normalized_secondary_conditions)
    secondary_columns_1 = [condition["source_column"] for condition in normalized_secondary_conditions]
    secondary_columns_2 = [condition["destination_column"] for condition in normalized_secondary_conditions]
    pass_columns_1 = [config[f] for config in passes for f in ("amount_source", "date_source", "narrative_source") if config.get(f)]
    pass_columns_2 = [config[f] for config in passes for f in ("amount_destination", "date_destination", "narrative_destination") if config.get(f)]
    allowed_f1_cols = set(file_1_match_keys + file_1_id_col + secondary_columns_1 + pass_columns_1 + [r[0][i] for r in normalized_rules for i in range(len(r[0]))] + file_1_extra)
    allowed_f2_cols = set(file_2_match_keys + file_2_id_col + secondary_columns_2 + pass_columns_2 + [r[1][i] for r in normalized_rules for i in range(len(r[1]))] + file_2_extra)

    def grouped_rows_note(row: dict) -> str:
        count = row.get("__GROUP_COUNT__", 1)
        return f"{count} rows combined (rows {row.get('__GROUPED_ROWS__', '')})" if count and count > 1 else ""

    def display_key(row: dict, key_columns: list[str], pass_columns: list[str]) -> str:
        columns = key_columns or list(dict.fromkeys(pass_columns))
        return " | ".join("" if is_blank(row.get(column)) else str(row.get(column)) for column in columns).strip(" |")

    def decision_keys(file_1_row: dict, file_2_row: dict) -> tuple[str, str]:
        """The values a reviewer sees (MATCH KEY / CANDIDATE KEY) and decides on."""
        return display_key(file_1_row, file_1_id_col, pass_columns_1), display_key(file_2_row, file_2_id_col, pass_columns_2)

    def rejected_note(file_1_row: dict, rejected_rows: list[dict], pass_name: str = "") -> str:
        """Explain why a remembered rejection was not proposed again."""
        if not rejected_rows:
            return ""
        _, candidate = decision_keys(file_1_row, rejected_rows[0])
        target = f"'{candidate}'" if len(rejected_rows) == 1 else f"{len(rejected_rows)} records"
        history = learning.history(*decision_keys(file_1_row, rejected_rows[0])) if learning is not None else None
        when = f" on {history.rejected_on}" if history and history.rejected_on else ""
        note = f"a reviewer rejected pairing this record with {target} before{when}, so it is not proposed again."
        return f"{pass_name}: {note}" if pass_name else note[0].upper() + note[1:]

    def key_normalization(file_1_row: dict, file_2_row: dict) -> list[str]:
        """Rules that made text keys or exact secondary keys equal."""
        applied: list[str] = []
        for source, destination, kind in zip(file_1_id_col, file_2_id_col, raw_key_types):
            if kind in _TEXT_LIKE_TYPES:
                applied += normalizer.explain(file_1_row.get(source), file_2_row.get(destination))
        for condition in normalized_secondary_conditions:
            if condition.get("comparison_method") == "exact_text":
                applied += normalizer.explain(
                    file_1_row.get(condition["source_column"]), file_2_row.get(condition["destination_column"])
                )
        return list(dict.fromkeys(applied))

    # ── Pass 1: primary key (and mandatory secondary keys) ────────────────
    file_1_records = file_1_df.to_dict('records')
    outcomes: list[dict] = []
    for row_idx, file_1_row in enumerate(file_1_records):
        if is_cancelled and row_idx % 25 == 0 and is_cancelled():
            raise InterruptedError("Reconciliation cancelled by user")

        outcome = {
            "row_idx": row_idx, "row": file_1_row, "classification": MatchClassification.NOT_FOUND,
            "key_result": None, "threshold": None, "best_idx": None, "file_2_row": None, "candidate_row": None,
            "secondary_mismatch": [], "pass_label": "", "explanation": None, "method": "", "learned_note": "",
            "key_partners": {},
        }
        outcomes.append(outcome)
        if not has_keys:
            continue

        file_1_id = (
            [file_1_row.get(key) for key in file_1_match_keys]
            if is_composite_key
            else file_1_row.get(primary_key_1)
        )

        exact_candidates = indexed_matcher.find_exact_candidates(file_1_id, matched_file_2_indices)
        classification = MatchClassification.NOT_FOUND
        key_result: MatchResult | None = None
        required_threshold: int | None = None
        best_idx = None
        file_2_row = None
        candidate_row = None
        secondary_mismatch: list[str] = []

        # Secondary keys are mandatory: a primary-key hit only counts when every
        # configured secondary key matches too (e.g. ID *and* vendor name).
        if exact_candidates and normalized_secondary_conditions:
            passing, _ = indexed_matcher.find_secondary_candidates(
                file_1_row,
                normalized_secondary_conditions,
                matched_file_2_indices,
            )
            passing_indices = {index for index, _ in passing}
            rejected = exact_candidates
            exact_candidates = [candidate for candidate in exact_candidates if candidate[0] in passing_indices]
            if not exact_candidates:
                candidate_row = rejected[0][1]
                secondary_mismatch = _secondary_mismatches(file_1_row, candidate_row, normalized_secondary_conditions)
                # Kept so a date-window pass can still pair the record with
                # this same-key partner (see the later passes).
                outcome["key_partners"] = {
                    index: set(_failed_secondary_conditions(file_1_row, row, normalized_secondary_conditions))
                    for index, row, _ in rejected
                }

        if len(exact_candidates) == 1:
            best_idx, file_2_row, key_result = exact_candidates[0]
            candidate_row = file_2_row
            classification = MatchClassification.EXACT_MATCH
        elif len(exact_candidates) > 1:
            classification = MatchClassification.AMBIGUOUS_MATCH
        elif secondary_mismatch:
            pass  # The primary key exists but its secondary keys differ: not a match.
        elif normalized_secondary_conditions:
            secondary_candidates, _ = indexed_matcher.find_secondary_candidates(
                file_1_row,
                normalized_secondary_conditions,
                matched_file_2_indices,
            )
            # Secondary keys alone never identify a record: a candidate must also
            # have a similar primary key. Large candidate sets (e.g. every row
            # of one vendor) are first cut down through the primary-key token
            # index, so the similarity check stays bounded.
            if len(secondary_candidates) > _MAX_DIRECT_SIMILARITY_CANDIDATES:
                similar_positions = indexed_matcher.token_candidate_indices(file_1_id)
                secondary_candidates = [candidate for candidate in secondary_candidates if candidate[0] in similar_positions]
            scored = []
            for candidate_idx, secondary_row in secondary_candidates:
                similarity_result, required_threshold = _primary_similarity_result(
                    file_1_row,
                    secondary_row,
                    file_1_id_col,
                    file_2_id_col,
                    key_matcher_types,
                    similarity_policy,
                )
                scored.append((candidate_idx, secondary_row, similarity_result))
            qualifying = [item for item in scored if item[2].confidence >= (required_threshold or 0)]
            if learning is not None and qualifying:
                # A pairing a reviewer rejected is never proposed again; if
                # exactly one other candidate remains, that one is proposed.
                remembered = [item for item in qualifying if learning.is_rejected(*decision_keys(file_1_row, item[1]))]
                if remembered:
                    qualifying = [item for item in qualifying if item not in remembered]
                    outcome["learned_note"] = rejected_note(file_1_row, [item[1] for item in remembered])
                    if not qualifying:
                        _, candidate_row, key_result = remembered[0]
            if len(qualifying) > 1:
                classification = MatchClassification.AMBIGUOUS_MATCH
            elif len(qualifying) == 1:
                best_idx, file_2_row, key_result = qualifying[0]
                candidate_row = file_2_row
                classification = MatchClassification.EXCEPTION_MATCH
            elif len(scored) == 1 and not outcome["learned_note"]:
                # One secondary candidate whose key is too different: explain why.
                _, candidate_row, key_result = scored[0]

        if file_2_row is not None:
            matched_file_2_indices.add(best_idx)
        method = ""
        if classification is MatchClassification.EXCEPTION_MATCH and key_result is not None:
            method = f"Similar key ({key_result.matcher_type.replace('_', ' ')})"
        outcome.update(
            classification=classification, key_result=key_result, threshold=required_threshold, best_idx=best_idx,
            file_2_row=file_2_row, candidate_row=candidate_row, secondary_mismatch=secondary_mismatch,
            pass_label="Primary key" if file_2_row is not None else "", method=method,
        )

    # ── Later passes: keyless matching on records still unmatched ─────────
    if passes:
        file_2_positions = {index: position for position, index in enumerate(file_2_df.index)}
        file_2_rows_all = file_2_df.to_dict('records')

        for number, config in enumerate(passes, start=2 if has_keys else 1):
            label = pass_label(config, number)
            # A date window replaces an exact-date secondary key on the same
            # columns; every other secondary key still applies.
            replaced = _conditions_replaced_by_pass(config, normalized_secondary_conditions)
            remaining_conditions = [
                condition for index, condition in enumerate(normalized_secondary_conditions) if index not in replaced
            ]
            # Ambiguous key matches and explicit secondary-key mismatches are
            # decisions for a person, never for a later, weaker pass. The one
            # exception: the key matched and only a replaced date differs. Such
            # a record may pair only with its same-key partner, never with an
            # unrelated record that happens to have the same amount.
            same_key_only: dict[int, set] = {}
            open_sources = []
            for outcome in outcomes:
                if outcome["file_2_row"] is not None or outcome["classification"] is not MatchClassification.NOT_FOUND:
                    continue
                if outcome["secondary_mismatch"]:
                    partners = {index for index, failed in outcome["key_partners"].items() if replaced and failed <= replaced}
                    if not partners:
                        continue
                    same_key_only[id(outcome["row"])] = partners
                open_sources.append((outcome["row_idx"], outcome["row"]))
            open_destinations = [
                (index, file_2_rows_all[file_2_positions[index]])
                for index in file_2_df.index
                if index not in matched_file_2_indices
            ]
            if not open_sources or not open_destinations:
                break
            destination_index = {id(row): index for index, row in open_destinations}
            respect = bool(remaining_conditions) and config.get("respect_secondary_keys", True)

            def check(source_row: dict, destination_row: dict, respect=respect, remaining=remaining_conditions,
                      same_key_only=same_key_only, destination_index=destination_index) -> bool:
                partners = same_key_only.get(id(source_row))
                if partners is not None and destination_index.get(id(destination_row)) not in partners:
                    return False
                return not (respect and _failed_secondary_conditions(source_row, destination_row, remaining))

            matches, ambiguities = run_pass(config, open_sources, open_destinations, check)
            for match in matches:
                outcome = outcomes[match.source_position]
                destination_row = file_2_rows_all[file_2_positions[match.destination_position]]
                if learning is not None and not match.exact and learning.is_rejected(*decision_keys(outcome["row"], destination_row)):
                    outcome["learned_note"] = rejected_note(outcome["row"], [destination_row], label)
                    continue
                matched_file_2_indices.add(match.destination_position)
                classification = MatchClassification.EXACT_MATCH if match.exact else MatchClassification.EXCEPTION_MATCH
                outcome.update(
                    classification=classification,
                    key_result=MatchResult(True, 100 if match.exact else 99, "keyless", label, match.detail),
                    best_idx=match.destination_position,
                    file_2_row=destination_row,
                    candidate_row=destination_row,
                    secondary_mismatch=[],
                    pass_label=label,
                    method="" if match.exact else label,
                    explanation=(
                        f"Matched in {label[0].lower()}{label[1:]}: {match.detail}."
                        + ("" if match.exact else " Please confirm.")
                    ),
                )
            for ambiguity in ambiguities:
                outcome = outcomes[ambiguity.source_position]
                if outcome["file_2_row"] is None:
                    outcome.update(
                        classification=MatchClassification.AMBIGUOUS_MATCH,
                        explanation=f"{label}: {ambiguity.detail}, so none was chosen.",
                    )

    # ── Results ────────────────────────────────────────────────────────────
    for outcome in outcomes:
        row_idx, file_1_row = outcome["row_idx"], outcome["row"]
        classification = outcome["classification"]
        key_result, file_2_row, candidate_row = outcome["key_result"], outcome["file_2_row"], outcome["candidate_row"]
        best_idx = outcome["best_idx"]
        normalization_applied = (
            key_normalization(file_1_row, file_2_row)
            if file_2_row is not None and outcome["pass_label"] == "Primary key"
            else []
        )
        unmatched_note = outcome["learned_note"] if file_2_row is None and classification is MatchClassification.NOT_FOUND else ""
        explanation = outcome["explanation"] or unmatched_note or _identity_explanation(
            classification,
            key_result,
            outcome["threshold"],
            secondary_names if normalized_secondary_conditions else None,
            outcome["secondary_mismatch"],
            normalization_applied if classification is MatchClassification.EXACT_MATCH else None,
        )
        if outcome["learned_note"] and not unmatched_note and file_2_row is not None:
            explanation = f"{explanation} ({outcome['learned_note']})"

        # What earlier decisions say, and whether a rule confirms the match.
        method = outcome["method"]
        review_history = learned_confidence = ""
        confirmed_by = None
        if classification is MatchClassification.EXCEPTION_MATCH and file_2_row is not None and key_result is not None:
            key_1, key_2 = decision_keys(file_1_row, file_2_row)
            if learning is not None:
                review_history, learned_confidence = learning.describe(key_1, key_2, method, key_result.confidence)
                if review_history:
                    explanation = f"{explanation} {review_history}."
                if learned_confidence:
                    explanation = f"{explanation} Learned confidence for this kind of match: {learned_confidence}."
            if evaluator is not None:
                confirmed_by = evaluator.confirmation(file_1_row, key_1, key_2, key_result.confidence, method)
            if confirmed_by is not None:
                classification = MatchClassification.EXACT_MATCH
                explanation = f"{explanation} Confirmed automatically by rule '{confirmed_by.label()}'."
                auto_resolved.append({
                    **resolution_fields(confirmed_by, "Match to confirm", "to_confirm"),
                    "Record": key_1, "Matched With": key_2, "How Matched": method,
                    "Match Confidence": f"{key_result.confidence}%", "ROW (FILE 1)": file_1_row.get("_ROW_NO", row_idx + 2),
                })

        identity_record = {
            "ROW (FILE 1)": file_1_row.get("_ROW_NO", row_idx + 2),
            "IDENTITY CLASSIFICATION": classification.value,
            "MATCH EXPLANATION": explanation,
            "MATCH TYPE": key_result.matcher_type if key_result else "",
            "MATCH CONFIDENCE": f"{key_result.confidence}%" if key_result else "0%",
            "MATCH THRESHOLD": f"{outcome['threshold']}%" if outcome["threshold"] is not None else "",
            # Raw key values make every audit row traceable without row lookups.
            "MATCH KEY": display_key(file_1_row, file_1_id_col, pass_columns_1),
            "CANDIDATE KEY": display_key(candidate_row, file_2_id_col, pass_columns_2) if candidate_row is not None else "",
        }
        if passes and outcome["pass_label"]:
            identity_record["MATCH PASS"] = outcome["pass_label"]
        if normalization_applied:
            identity_record["NORMALIZATION APPLIED"] = ", ".join(normalization_applied)
        learned_fields = {
            name: value
            for name, value in (
                ("MATCH METHOD", method), ("REVIEW HISTORY", review_history), ("LEARNED CONFIDENCE", learned_confidence),
                ("AUTO-RESOLVED BY", confirmed_by.label() if confirmed_by else ""),
            )
            if value
        }
        identity_record.update(learned_fields)
        identity_resolution.append(identity_record)

        if file_2_row is None or key_result is None:
            clean_f1_row = {k: v for k, v in file_1_row.items() if k in allowed_f1_cols}
            clean_f1_row.update(original_values(file_1_row, transformed_1.originals))
            clean_f1_row["ROW (FILE 1)"] = file_1_row.get("_ROW_NO", row_idx + 2)
            # Ambiguous records are never auto-resolved: a person decides.
            rule = evaluator.unmatched(file_1_row, "source") if evaluator and classification is MatchClassification.NOT_FOUND else None
            if rule is not None:
                identity_record["AUTO-RESOLVED BY"] = rule.label()
                auto_resolved.append({
                    **resolution_fields(rule, f"Only in {file_1_name}", "only_in_source"),
                    "Record": identity_record["MATCH KEY"], **clean_f1_row, **referenced_values(rule, file_1_row),
                })
                continue
            clean_f1_row["IDENTITY CLASSIFICATION"] = classification.value
            clean_f1_row["MATCH EXPLANATION"] = explanation
            if note := grouped_rows_note(file_1_row):
                clean_f1_row["GROUPED ROWS"] = note
            file_1_not_found.append(clean_f1_row)
            continue

        # Consolidated rows on either side: many-to-one / one-to-many / many-to-many.
        grouped_1 = file_1_row.get("__GROUP_COUNT__", 1) > 1
        grouped_2 = file_2_row.get("__GROUP_COUNT__", 1) > 1
        group_status = {
            (False, False): "One-to-One Match",
            (True, False): "Many-to-One Match",
            (False, True): "One-to-Many Match",
            (True, True): "Many-to-Many Match",
        }[(grouped_1, grouped_2)]

        reconciliation_result = {
            "ROW (FILE 1)": file_1_row.get("_ROW_NO", row_idx + 2),
            "ROW (FILE 2)": file_2_row.get("_ROW_NO", (best_idx if isinstance(best_idx, int) else 0) + 2),
            "MATCH TYPE": key_result.matcher_type,
            "MATCH CONFIDENCE": f"{key_result.confidence}%",
            "MATCH STATUS": key_result.status,
            "IDENTITY CLASSIFICATION": classification.value,
            "MATCH EXPLANATION": explanation,
            "GROUP CLASSIFICATION": group_status,
        }
        if passes:
            reconciliation_result["MATCH PASS"] = outcome["pass_label"]
        reconciliation_result.update(learned_fields)
        if not has_keys:
            reconciliation_result["MATCH KEY"] = display_key(file_1_row, [], pass_columns_1)
            reconciliation_result["MATCHED KEY"] = display_key(file_2_row, [], pass_columns_2)
        elif is_composite_key:
            reconciliation_result["COMPOSITE MATCH KEY"] = display_key(file_1_row, file_1_id_col, [])
            reconciliation_result["MATCHED COMPOSITE KEY"] = display_key(file_2_row, file_2_id_col, [])
            for source_column, destination_column in zip(file_1_id_col, file_2_id_col):
                reconciliation_result[source_column] = file_1_row.get(source_column)
                reconciliation_result[f"MATCHED {destination_column}"] = file_2_row.get(destination_column)
        else:
            reconciliation_result[primary_key_1] = file_1_row.get(primary_key_1)
            reconciliation_result[f"MATCHED {primary_key_2}"] = file_2_row.get(primary_key_2)
            # Raw (display) key values; the NORM_ columns above hold match forms.
            reconciliation_result["MATCH KEY"] = None if is_blank(file_1_row.get(file_1_id_col[0])) else file_1_row.get(file_1_id_col[0])
            reconciliation_result["MATCHED KEY"] = file_2_row.get(file_2_id_col[0])
        if secondary_columns_1:
            reconciliation_result["SECONDARY KEY"] = " | ".join(str(file_1_row.get(column, "")) for column in secondary_columns_1)
            reconciliation_result["MATCHED SECONDARY KEY"] = " | ".join(str(file_2_row.get(column, "")) for column in secondary_columns_2)
        grouped_notes = [
            f"{label}: {note}"
            for label, row in (("File 1", file_1_row), ("File 2", file_2_row))
            if (note := grouped_rows_note(row))
        ]
        if grouped_notes:
            reconciliation_result["GROUPED ROWS"] = "; ".join(grouped_notes)

        for col in file_1_extra:
            if col != "_ROW_NO":
                reconciliation_result[col] = file_1_row.get(col)
        for col in file_2_extra:
            if col != "_ROW_NO":
                reconciliation_result[f"{col} (FILE 2)"] = file_2_row.get(col)

        has_field_difference = False
        field_rules: list[str] = []
        resolved_differences: list[str] = []
        for file_1_fields, file_2_fields in normalized_rules:
            differences = compare_rule_values(file_1_row, file_2_row, file_1_fields, file_2_fields, field_rules)
            if differences and evaluator is not None:
                label = fields_label(file_1_fields)
                value_1, value_2 = differences.get(f"{label} (FILE 1)"), differences.get(f"{label} (FILE 2)")
                difference = differences.get(f"{label} DIFF")
                rule = evaluator.difference(label, value_1, value_2, difference, file_1_row)
                if rule is not None:
                    resolved_differences.append(f"{label}: {rule.resolution} (rule '{rule.label()}')")
                    auto_resolved.append({
                        **resolution_fields(rule, "Field difference", "field_difference"),
                        "Record": decision_keys(file_1_row, file_2_row)[0], "Field": label,
                        "File 1 Value": value_1, "File 2 Value": value_2, "Difference": difference,
                        "ROW (FILE 1)": file_1_row.get("_ROW_NO", row_idx + 2),
                    })
                    continue
            if differences:
                has_field_difference = True
                reconciliation_result.update(differences)
        if resolved_differences:
            reconciliation_result["RESOLVED DIFFERENCES"] = "; ".join(resolved_differences)
        if has_field_difference:
            field_discrepancy_count += 1
        all_rules = list(dict.fromkeys(normalization_applied + field_rules))
        if all_rules:
            reconciliation_result["NORMALIZATION APPLIED"] = ", ".join(all_rules)
            for rule in all_rules:
                normalization_counts[rule] = normalization_counts.get(rule, 0) + 1
        reconciliation_result.update(original_values(file_1_row, transformed_1.originals, " (FILE 1)"))
        reconciliation_result.update(original_values(file_2_row, transformed_2.originals, " (FILE 2)"))

        if has_field_difference or classification is MatchClassification.EXCEPTION_MATCH:
            reconciliation_results.append(reconciliation_result)
        else:
            matched_records.append(reconciliation_result)

    # Tolerance bands: accept small differences now that matching is done,
    # before anything is counted or written.
    tolerance = apply_tolerance_bands(
        reconciliation_results, matched_records, tolerance_bands, compared_labels,
        stays_in_review=lambda record: record["IDENTITY CLASSIFICATION"] == MatchClassification.EXCEPTION_MATCH.value,
    )
    field_discrepancy_count -= tolerance.cleared_records

    tracker.comparing_columns()

    if is_cancelled and is_cancelled():
        raise InterruptedError("Reconciliation cancelled by user")

    file_2_indices_set = set(file_2_df.index)
    unmatched_indices = file_2_indices_set - matched_file_2_indices
    file_2_records = file_2_df.to_dict('records')
    file_2_not_found = []
    for i, idx in enumerate(file_2_df.index):
        if idx in unmatched_indices:
            clean_f2_row = {k: v for k, v in file_2_records[i].items() if k in allowed_f2_cols}
            clean_f2_row.update(original_values(file_2_records[i], transformed_2.originals))
            clean_f2_row["ROW (FILE 2)"] = file_2_records[i].get("_ROW_NO", idx + 2)
            rule = evaluator.unmatched(file_2_records[i], "destination") if evaluator is not None else None
            if rule is not None:
                auto_resolved.append({
                    **resolution_fields(rule, f"Only in {file_2_name}", "only_in_destination"),
                    "Record": display_key(file_2_records[i], file_2_id_col, pass_columns_2),
                    **clean_f2_row, **referenced_values(rule, file_2_records[i]),
                })
                continue
            if note := grouped_rows_note(file_2_records[i]):
                clean_f2_row["GROUPED ROWS"] = note
            file_2_not_found.append(clean_f2_row)

    tracker.generating_report()

    pass_counts: dict[str, int] = {}
    for outcome in outcomes:
        if outcome["pass_label"]:
            pass_counts[outcome["pass_label"]] = pass_counts.get(outcome["pass_label"], 0) + 1

    universal_data = build_universal_data_model(
        job_type="generic",
        file_1_name=file_1_name,
        file_2_name=file_2_name,
        matching_keys=file_1_id_col,
        reconciliation_results=reconciliation_results,
        file_1_not_found=file_1_not_found,
        file_2_not_found=file_2_not_found,
        matched_records=matched_records,
        total_file_1=len(file_1_df),
        total_file_2=len(file_2_df),
        identity_resolution=identity_resolution,
        secondary_keys=[
            f"{condition['source_column']} ↔ {condition['destination_column']} ({str(condition.get('comparison_method', '')).replace('_', ' ')})"
            for condition in normalized_secondary_conditions
        ],
        compared_fields=[f"{fields_label(left)} ↔ {fields_label(right)}" for left, right in normalized_rules],
        extra_metadata={
            "transformations": transformed_1.applied + [
                step for step in transformed_2.applied if step not in transformed_1.applied
            ],
            "transformations_source": transformed_1.applied,
            "transformations_destination": transformed_2.applied,
            "matching_passes": (["Primary key"] if has_keys else []) + [
                pass_label(config, number) for number, config in enumerate(passes, start=2 if has_keys else 1)
            ],
            "pass_counts": pass_counts,
            "normalization_rules_used": normalization_counts,
            "date_convention": "Day first (DD/MM/YYYY)" if date_dayfirst else "Month first (MM/DD/YYYY)",
            "resolution_rules": [f"{rule.label()}: {describe_rule(rule)}" for rule in (evaluator.rules if evaluator else [])],
            "resolution_rules_used": dict(evaluator.counts) if evaluator else {},
            "tolerance_bands": [band.describe() for band in tolerance_bands],
            "tolerance_counts": {"fields": tolerance.accepted_fields, "records": tolerance.moved_to_matched},
        },
    )
    universal_data["auto_resolved"] = auto_resolved

    if write_report:
        generate_enterprise_report(universal_data, {}, output_path)

    tracker.finalized()

    summary_stats = {
        "report_rows": len(reconciliation_results),
        "only_in_file_1": len(file_1_not_found),
        "only_in_file_2": len(file_2_not_found),
        "confidence_review": sum(
            1 for row in reconciliation_results if int(str(row.get("MATCH CONFIDENCE", "100")).replace("%", "") or 0) < 100
        ),
        "source_records": len(file_1_df),
        "destination_records": len(file_2_df),
        "matched_records": len(matched_file_2_indices),
        "fully_matched_records": len(matched_file_2_indices) - len(reconciliation_results),
        "field_discrepancies": field_discrepancy_count,
        "exact_matches": sum(item["IDENTITY CLASSIFICATION"] == MatchClassification.EXACT_MATCH.value for item in identity_resolution),
        "exception_matches": sum(item["IDENTITY CLASSIFICATION"] == MatchClassification.EXCEPTION_MATCH.value for item in identity_resolution),
        "ambiguous_matches": sum(item["IDENTITY CLASSIFICATION"] == MatchClassification.AMBIGUOUS_MATCH.value for item in identity_resolution),
        "not_found_matches": sum(item["IDENTITY CLASSIFICATION"] == MatchClassification.NOT_FOUND.value for item in identity_resolution),
        "keyless_matches": sum(count for label, count in pass_counts.items() if label != "Primary key"),
        "normalized_matches": sum(1 for item in identity_resolution if item.get("NORMALIZATION APPLIED")),
        "auto_resolved": len(auto_resolved),
        "auto_resolved_only_in_file_1": sum(1 for item in auto_resolved if item["__KIND__"] == "only_in_source"),
        "auto_resolved_only_in_file_2": sum(1 for item in auto_resolved if item["__KIND__"] == "only_in_destination"),
        "auto_resolved_differences": sum(1 for item in auto_resolved if item["__KIND__"] == "field_difference"),
        "auto_confirmed_matches": sum(1 for item in auto_resolved if item["__KIND__"] == "to_confirm"),
        "within_tolerance_fields": tolerance.accepted_fields,
        "within_tolerance_records": tolerance.moved_to_matched,
    }

    return {
        **summary_stats,
        "summary": summary_stats,
        "universal_data": universal_data,
    }


def run_gst_reconciliation(
    file_1_df,
    file_2_df,
    output_path: Path,
    orientation: str = "vertical",
    text_threshold: int = GST_TEXT_REVIEW_THRESHOLD,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    file_1_name: str = "File1",
    file_2_name: str = "File2",
    is_cancelled: Optional[Callable[[], bool]] = None,
    write_report: bool = True,
) -> dict:
    tracker = ProgressTracker(progress_callback)
    tracker.reading_excel()

    if isinstance(file_1_df, (Path, str)):
        raw1 = pd.read_excel(file_1_df)
    else:
        raw1 = file_1_df.copy(deep=False) if hasattr(file_1_df, 'copy') else file_1_df

    if isinstance(file_2_df, (Path, str)):
        raw2 = pd.read_excel(file_2_df)
    else:
        raw2 = file_2_df.copy(deep=False) if hasattr(file_2_df, 'copy') else file_2_df

    if "_ROW_NO" not in raw1.columns:
        raw1["_ROW_NO"] = raw1.index + 2
    if "_ROW_NO" not in raw2.columns:
        raw2["_ROW_NO"] = raw2.index + 2

    df1 = normalise_gst_df(prepare_dataframe(raw1, orientation=orientation), GST_AMOUNT_COLUMNS)
    df2 = normalise_gst_df(prepare_dataframe(raw2, orientation=orientation), GST_AMOUNT_COLUMNS)

    missing_file1 = validate_gst_columns(df1)
    missing_file2 = validate_gst_columns(df2)
    if missing_file1 or missing_file2:
        raise KeyError(
            "Missing GST columns - "
            f"File 1: {', '.join(missing_file1) or 'None'}; "
            f"File 2: {', '.join(missing_file2) or 'None'}"
        )

    df1 = merge_duplicate_invoices(df1, GST_MERGE_KEY_COLUMNS, GST_AMOUNT_COLUMNS)
    df2 = merge_duplicate_invoices(df2, GST_MERGE_KEY_COLUMNS, GST_AMOUNT_COLUMNS)

    if is_cancelled and is_cancelled():
        raise InterruptedError("Reconciliation cancelled by user")

    tracker.building_indexes()

    gstr_groups_2 = {gstr: group for gstr, group in df2.groupby("GSTR")}
    gstr_matchers_2 = {
        gstr: IndexedCandidateMatcher(group, "INVOICE NO.", "invoice")
        for gstr, group in gstr_groups_2.items()
    }

    mismatched: list[dict] = []
    only_in_file1: list[dict] = []
    confidence_review: list[dict] = []
    matched_records: list[dict] = []
    matched_file2_indices: set = set()

    tracker.matching_records()

    df1_records = df1.to_dict('records')
    for row_idx, row1 in enumerate(df1_records):
        if is_cancelled and row_idx % 25 == 0 and is_cancelled():
            raise InterruptedError("Reconciliation cancelled by user")

        gstr = row1.get("GSTR")

        matcher = gstr_matchers_2.get(gstr)
        if matcher is None:
            clean_r1 = {k: v for k, v in row1.items() if k != "_ROW_NO"}
            clean_r1["ROW (File 1)"] = row1.get("_ROW_NO", row_idx + 2)
            only_in_file1.append(clean_r1)
            continue

        best_idx, row2, invoice_result = matcher.find_best_match(
            row1.get("INVOICE NO."),
            "INVOICE NO.",
            matched_file2_indices,
        )

        if row2 is None or invoice_result is None:
            clean_r1 = {k: v for k, v in row1.items() if k != "_ROW_NO"}
            clean_r1["ROW (File 1)"] = row1.get("_ROW_NO", row_idx + 2)
            only_in_file1.append(clean_r1)
            continue

        matched_file2_indices.add(best_idx)
        base = {
            "ROW (File 1)": row1.get("_ROW_NO", row_idx + 2),
            "ROW (File 2)": row2.get("_ROW_NO", best_idx + 2),
            "GSTR": gstr,
            "INVOICE NO. (File1)": row1.get("INVOICE NO."),
            "INVOICE NO. (File2)": row2.get("INVOICE NO."),
            "INVOICE MATCH CONFIDENCE": f"{invoice_result.confidence}%",
            "INVOICE MATCH STATUS": invoice_result.status,
            "NAME (File1)": row1.get("NAME OF TRADER/FIRM/COMPANY", ""),
            "NAME (File2)": row2.get("NAME OF TRADER/FIRM/COMPANY", ""),
        }

        field_diffs: dict[str, object] = {}
        review_notes: dict[str, object] = {}

        name_result = compare_values(
            row1.get("NAME OF TRADER/FIRM/COMPANY", ""),
            row2.get("NAME OF TRADER/FIRM/COMPANY", ""),
            "NAME OF TRADER/FIRM/COMPANY",
            "NAME OF TRADER/FIRM/COMPANY",
            "company_name",
        )
        if not name_result.matched or name_result.confidence < text_threshold:
            field_diffs["NAME STATUS"] = name_result.status
            field_diffs["NAME CONFIDENCE"] = f"{name_result.confidence}%"
        elif name_result.confidence < 100:
            review_notes["NAME STATUS"] = name_result.status
            review_notes["NAME CONFIDENCE"] = f"{name_result.confidence}%"

        date_result = compare_values(row1.get("INVOICE DATE"), row2.get("INVOICE DATE"), "INVOICE DATE", "INVOICE DATE", "date")
        if not date_result.matched:
            field_diffs["DATE (File1)"] = row1.get("INVOICE DATE")
            field_diffs["DATE (File2)"] = row2.get("INVOICE DATE")
            field_diffs["DATE STATUS"] = date_result.status
            field_diffs["DATE DETAIL"] = date_result.detail

        for col in GST_AMOUNT_COLUMNS:
            amount_result = compare_values(row1.get(col, 0), row2.get(col, 0), col, col, "numeric")
            if not amount_result.matched:
                value1 = float(row1.get(col, 0) or 0)
                value2 = float(row2.get(col, 0) or 0)
                field_diffs[f"{col} (File1)"] = value1
                field_diffs[f"{col} (File2)"] = value2
                field_diffs[f"{col} DIFF"] = round(value1 - value2, 2)

        if field_diffs:
            record = dict(base)
            record.update(field_diffs)
            mismatched.append(record)
        elif invoice_result.confidence < 100 or review_notes:
            record = dict(base)
            record.update(review_notes)
            confidence_review.append(record)
        else:
            record = dict(base)
            matched_records.append(record)

    tracker.comparing_columns()

    if is_cancelled and is_cancelled():
        raise InterruptedError("Reconciliation cancelled by user")

    df2_indices_set = set(df2.index)
    unmatched_df2_indices = df2_indices_set - matched_file2_indices
    df2_records = df2.to_dict('records')
    only_in_file2 = []
    for i, idx in enumerate(df2.index):
        if idx in unmatched_df2_indices:
            clean_r2 = {k: v for k, v in df2_records[i].items() if k != "_ROW_NO"}
            clean_r2["ROW (File 2)"] = df2_records[i].get("_ROW_NO", idx + 2)
            only_in_file2.append(clean_r2)

    tracker.generating_report()
    
    # We combine mismatched + confidence_review for the universal model
    combined_mismatched = mismatched + confidence_review
    
    universal_data = build_universal_data_model(
        job_type="gst",
        file_1_name=file_1_name,
        file_2_name=file_2_name,
        matching_keys=["GSTR", "INVOICE NO."],
        reconciliation_results=combined_mismatched,
        file_1_not_found=only_in_file1,
        file_2_not_found=only_in_file2,
        matched_records=matched_records,
        total_file_1=len(df1),
        total_file_2=len(df2),
    )

    if write_report:
        # The GST public engine has historically emitted this audit artifact.
        # Generic job execution persists its merged JSON in the service layer.
        from app.utils.json_encoder import safe_json_dump

        raw_path = output_path.with_name(f"{output_path.stem}_data.json")
        with open(raw_path, "w", encoding="utf-8") as raw_file:
            safe_json_dump(universal_data, raw_file)
        generate_enterprise_report(universal_data, {}, output_path)
    
    tracker.finalized()

    summary_stats = {
        "report_rows": len(mismatched),
        "only_in_file_1": len(only_in_file1),
        "only_in_file_2": len(only_in_file2),
        "confidence_review": len(confidence_review),
        "source_records": len(df1),
        "destination_records": len(df2),
        "matched_records": len(matched_file2_indices),
        "fully_matched_records": len(matched_file2_indices) - len(mismatched) - len(confidence_review),
    }

    return {
        **summary_stats,
        "summary": summary_stats,
        "universal_data": universal_data,
    }
