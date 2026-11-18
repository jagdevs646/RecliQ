from __future__ import annotations

import time
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from app.reconciliation_engine.cache import (
    is_blank,
    normalize_header,
    normalize_text,
    parse_date_value,
    infer_decimal_precision,
    detect_number_format,
    to_decimal,
    to_number,
    tokens,
    use_date_convention,
)
from app.reconciliation_engine.matching import (
    IndexedCandidateMatcher,
    MatchClassification,
    MatchResult,
    compare_values,
    detect_compared_field_type,
    detect_matcher_type,
    detect_primary_key_type,
    match_threshold,
    non_empty_values,
)
from app.reconciliation_engine.matching.indexed_matcher import (
    composite_identity_key,
    detect_key_matcher_type,
    normalized_identity_value,
    normalized_key_identity,
)
from app.reconciliation_engine.column_engine import (
    DatePairComparison,
    IdentifierPairComparison,
    NumericPairComparison,
    TextPairComparison,
    compare_date_pairs,
    compare_identifier_pairs,
    compare_numeric_pairs,
    compare_text_pairs,
    unique_exact_pair_positions,
)
from app.reconciliation_engine.explanations import (
    EXPLANATION_CONTEXT_KEY,
    EXPLANATION_METADATA_KEY,
    explanation_metadata,
)
from app.reconciliation_engine.matching.pass_matcher import pass_label, run_column_pass, run_pass, validate_pass
from app.reconciliation_engine.matching.reference_variants import (
    DEFAULT_REFERENCE_LEVEL,
    SUGGESTION_MIN_LEAD,
    SUGGESTION_MIN_SCORE,
    VARIANT_KEY_TYPES,
    VARIANT_RULES,
    ReferenceSuggester,
    key_forms,
    reference_level,
    suggestion_form,
    years_compatible,
)
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
from app.reconciliation_engine.normalization.entities import EntityNormalizer
from app.reconciliation_engine.tolerance import Band, accepted_reason, apply_tolerance_bands, prepare_bands
from app.reconciliation_engine.transformations import TransformationResult, apply_transformations, original_values
from app.reconciliation_engine.learning import LearningContext
from app.reconciliation_engine.resolution import Evaluator, describe_rule, referenced_values, resolution_fields
from app.reconciliation_engine.matching.advanced_matcher import consolidate_duplicate_keys
from app.reconciliation_engine.progress_tracker import ProgressTracker
from app.reconciliation_engine.report_generator import (
    write_generic_report,
    write_gst_output,
)
from app.reconciliation_engine.record_collections import (
    ColumnarRecordRows,
    GeneratedRecordRows,
    concatenate_record_rows,
    count_populated_field,
)
from app.reconciliation_engine.utilities import (
    collect_rule_value,
    infer_combined_column_types,
    validate_columns,
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
_ENGINE_VERSIONS = {"rows": "rows-v1", "columns": "columns-v1"}


@dataclass
class _GenericReconciliationRequest:
    file_1_df: pd.DataFrame
    file_2_df: pd.DataFrame
    output_path: Path
    key_file_1: list[str] | None
    key_file_2: list[str] | None
    rules: list[dict]
    include_columns_file_1: list[str] | None
    include_columns_file_2: list[str] | None
    progress_callback: Optional[Callable[[int, str], None]]
    file_1_name: str
    file_2_name: str
    is_cancelled: Optional[Callable[[], bool]]
    write_report: bool
    secondary_conditions: list[dict] | None
    similarity_policy: dict | None
    date_only_override: bool
    transformations: list[dict] | None
    matching_passes: list[dict] | None
    normalizer: EntityNormalizer
    date_dayfirst: bool
    evaluator: Evaluator | None
    learning: LearningContext | None
    tolerances: list[dict] | None
    engine_mode: str


@dataclass
class _PreparedGenericReconciliation:
    request: _GenericReconciliationRequest
    tracker: ProgressTracker
    transformed_1: TransformationResult
    transformed_2: TransformationResult
    file_1_df: pd.DataFrame
    file_2_df: pd.DataFrame
    file_1_id_col: list[str]
    file_2_id_col: list[str]
    passes: list[dict]
    has_keys: bool
    normalized_rules: list[tuple[list[str], list[str]]]
    combination_types: list[tuple[str | None, str | None]]
    file_1_extra: list[str]
    file_2_extra: list[str]
    normalized_secondary_conditions: list[dict]
    compared_labels: list[tuple[str, str]]
    tolerance_bands: list[Band]
    comparison_types: list[str]
    comparison_precisions: list[int | None]
    precision_metadata: list[dict[str, Any]]
    number_formats_1: list[str | None]
    number_formats_2: list[str | None]
    number_format_metadata: list[dict[str, Any]]
    raw_key_types: list[str]
    file_1_match_keys: list[str]
    file_2_match_keys: list[str]
    key_matcher_types: list[str]
    indexed_matcher: IndexedCandidateMatcher | None
    file_2_records: list[dict[str, Any]] | None
    is_composite_key: bool
    primary_key_1: str | None
    primary_key_2: str | None
    secondary_names: str
    secondary_columns_1: list[str]
    secondary_columns_2: list[str]
    pass_columns_1: list[str]
    pass_columns_2: list[str]
    display_key_columns_1: list[str]
    display_key_columns_2: list[str]
    display_keys_nonblank_1: bool
    display_keys_nonblank_2: bool
    allowed_f1_cols: set[str]
    allowed_f2_cols: set[str]
    reference_level: str = DEFAULT_REFERENCE_LEVEL

    def grouped_rows_note(self, row: dict[str, Any]) -> str:
        count = row.get("__GROUP_COUNT__", 1)
        return f"{count} rows combined (rows {row.get('__GROUPED_ROWS__', '')})" if count and count > 1 else ""

    @staticmethod
    def display_key(row: dict[str, Any], columns: list[str], values_nonblank: bool = False) -> str:
        if values_nonblank:
            return " | ".join(str(row.get(column)) for column in columns)
        return " | ".join("" if is_blank(row.get(column)) else str(row.get(column)) for column in columns).strip(" |")

    def decision_keys(self, file_1_row: dict[str, Any], file_2_row: dict[str, Any]) -> tuple[str, str]:
        """The values a reviewer sees (MATCH KEY / CANDIDATE KEY) and decides on."""
        return (
            self.display_key(file_1_row, self.display_key_columns_1, self.display_keys_nonblank_1),
            self.display_key(file_2_row, self.display_key_columns_2, self.display_keys_nonblank_2),
        )

    def rejected_note(self, file_1_row: dict[str, Any], rejected_rows: list[dict[str, Any]], pass_name: str = "") -> str:
        """Explain why a remembered rejection was not proposed again."""
        if not rejected_rows:
            return ""
        _, candidate = self.decision_keys(file_1_row, rejected_rows[0])
        target = f"'{candidate}'" if len(rejected_rows) == 1 else f"{len(rejected_rows)} records"
        learning = self.request.learning
        history = learning.history(*self.decision_keys(file_1_row, rejected_rows[0])) if learning is not None else None
        when = f" on {history.rejected_on}" if history and history.rejected_on else ""
        note = f"a reviewer rejected pairing this record with {target} before{when}, so it is not proposed again."
        return f"{pass_name}: {note}" if pass_name else note[0].upper() + note[1:]

    def key_normalization(self, file_1_row: dict[str, Any], file_2_row: dict[str, Any]) -> list[str]:
        """Rules that made text keys or exact secondary keys equal."""
        applied: list[str] = []
        for source, destination, kind in zip(self.file_1_id_col, self.file_2_id_col, self.raw_key_types):
            if kind in _TEXT_LIKE_TYPES:
                applied += self.request.normalizer.explain(file_1_row.get(source), file_2_row.get(destination))
            elif kind in _IDENTIFIER_TYPES and _identifier_formatting_matches(
                file_1_row.get(source), file_2_row.get(destination), source, destination, kind
            ):
                applied.append(_identifier_rule_note(self.reference_level))
        for condition in self.normalized_secondary_conditions:
            if condition.get("comparison_method") == "exact_text":
                source = file_1_row.get(condition["source_column"])
                destination = file_2_row.get(condition["destination_column"])
                kind = condition.get("matcher_type") or detect_key_matcher_type(
                    [source, destination], condition["source_column"], condition["destination_column"]
                )
                if kind in _IDENTIFIER_TYPES and _identifier_formatting_matches(
                    source, destination, condition["source_column"], condition["destination_column"], kind
                ):
                    applied.append("Identifier punctuation, spacing, and case ignored")
                else:
                    applied += self.request.normalizer.explain(source, destination)
        return list(dict.fromkeys(applied))


@dataclass
class _GenericMatchState:
    outcomes: list[dict[str, Any]] | None
    matched_file_2_indices: set[object] | None
    column_exact_pairs: "_ColumnExactMatchState | None" = None


@dataclass
class _ColumnExactMatchState:
    """Compact exact-pair state for the safe all-column fast path."""

    source_positions: np.ndarray
    destination_positions: np.ndarray
    destination_by_source: np.ndarray
    destination_matched: np.ndarray
    key_normalization_rules: dict[int, tuple[str, ...]]
    text_batches: dict[int, TextPairComparison]
    numeric_batches: dict[int, NumericPairComparison]
    identifier_batches: dict[int, IdentifierPairComparison]
    date_batches: dict[int, DatePairComparison]


def validate_gst_columns(df: pd.DataFrame) -> list[str]:
    return [col for col in REQUIRED_GST_COLUMNS if col not in df.columns]


# Column-name types that are never amounts, even when the cell holds a number
# ("Invoice No" 0 vs blank is a missing identifier, not a zero amount).
_NON_AMOUNT_NAME_TYPES = {"date", "invoice", "gstin", "pan", "identifier", "company_name", "person_name"}
_IDENTIFIER_TYPES = {"invoice", "gstin", "pan", "identifier"}


def _identifier_rule_note(level: str) -> str:
    """How identifier keys were normalized at the configured reference level."""
    if level == "exact":
        return "Identifier spacing and case ignored"
    return "Identifier punctuation, spacing, and case ignored"


def _identifier_formatting_matches(
    value_1: object,
    value_2: object,
    column_1: str,
    column_2: str,
    matcher_type: str,
) -> bool:
    identity_1 = normalized_key_identity(value_1, column_1, column_2, matcher_type)
    identity_2 = normalized_key_identity(value_2, column_1, column_2, matcher_type)
    return identity_1 is not None and identity_1 == identity_2 and str(value_1) != str(value_2)


def _raw_key_values_are_identical(
    file_1_row: dict[str, Any],
    file_2_row: dict[str, Any],
    file_1_columns: list[str],
    file_2_columns: list[str],
) -> bool:
    """Whether key explanations can be skipped without changing their output."""
    for source, destination in zip(file_1_columns, file_2_columns):
        value_1, value_2 = file_1_row.get(source), file_2_row.get(destination)
        if type(value_1) is not type(value_2):
            return False
        try:
            if not bool(value_1 == value_2):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _blank_as_zero(
    file_1_value: object,
    file_2_value: object,
    file_1_fields: list[str],
    file_2_fields: list[str],
    number_format_file_1: str | None = None,
    number_format_file_2: str | None = None,
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
    populated_format = number_format_file_2 if blank_1 else number_format_file_1
    if to_number(populated, populated_format) is None:
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
    matcher_type_override: str | None = None,
    decimal_precision: int | None = None,
    number_format_file_1: str | None = None,
    number_format_file_2: str | None = None,
    combination_type_file_1: str | None = None,
    combination_type_file_2: str | None = None,
) -> dict:
    file_1_value, type_hint_1 = collect_rule_value(
        file_1_row, file_1_fields, number_format_file_1, matcher_type_override, combination_type_file_1
    )
    file_2_value, type_hint_2 = collect_rule_value(
        file_2_row, file_2_fields, number_format_file_2, matcher_type_override, combination_type_file_2
    )
    label = fields_label(file_1_fields)
    # A blank side has no type of its own; the populated side decides.
    matcher_type = matcher_type_override or type_hint_1 or type_hint_2

    try:
        identical_as_read = type(file_1_value) is type(file_2_value) and bool(file_1_value == file_2_value)
    except Exception:
        identical_as_read = False
    if identical_as_read and (
        matcher_type != "numeric" or number_format_file_1 == number_format_file_2
    ):
        return {}

    if matcher_type is None:
        matcher_type = detect_matcher_type(
            [file_1_value, file_2_value],
            fields_label(file_1_fields),
            fields_label(file_2_fields),
        )

    compared_1, compared_2 = file_1_value, file_2_value
    if matcher_type == "numeric":
        compared_1, compared_2 = _blank_as_zero(
            file_1_value,
            file_2_value,
            file_1_fields,
            file_2_fields,
            number_format_file_1,
            number_format_file_2,
        )

    result = compare_values(
        compared_1,
        compared_2,
        fields_label(file_1_fields),
        fields_label(file_2_fields),
        matcher_type,
        decimal_precision=decimal_precision,
        number_format1=number_format_file_1,
        number_format2=number_format_file_2,
    )

    if rules_sink is not None and result.rules:
        rules_sink.extend(result.rules)
    if result.matched and result.confidence == 100:
        return {}

    if result.matcher_type == "numeric":
        value_1 = to_decimal(file_1_value, number_format_file_1) or Decimal(0)
        value_2 = to_decimal(file_2_value, number_format_file_2) or Decimal(0)
        return {
            f"{label} (FILE 1)": file_1_value,
            f"{label} (FILE 2)": file_2_value,
            f"{label} DIFF": float(value_1 - value_2),
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


def _infer_secondary_key_types(
    file_1_df: pd.DataFrame,
    file_2_df: pd.DataFrame,
    conditions: list[dict],
) -> None:
    """Resolve one shared matcher type per exact secondary-key column pair."""
    for condition in conditions:
        if condition.get("comparison_method") != "exact_text":
            continue
        source_column = condition["source_column"]
        destination_column = condition["destination_column"]
        values = non_empty_values(
            list(file_1_df[source_column].head(500)) + list(file_2_df[destination_column].head(500)),
            limit=200,
        )
        condition["matcher_type"] = detect_key_matcher_type(
            values, source_column, destination_column
        )


# Up to this many secondary-key candidates are scored directly on primary-key
# similarity; larger sets are pre-filtered through the primary-key token index.
_MAX_DIRECT_SIMILARITY_CANDIDATES = 25


def _secondary_group_value(condition: dict):
    """Deterministic normal form of a secondary key, or None if not groupable."""
    method = str(condition.get("comparison_method", ""))
    if method == "exact_text":
        source_column = str(condition.get("source_column", ""))
        destination_column = str(condition.get("destination_column", ""))
        matcher_type = condition.get("matcher_type")
        return lambda value: normalized_key_identity(
            value, source_column, destination_column, matcher_type
        )
    if method == "normalized_date":
        return lambda value: parsed.isoformat() if (parsed := parse_date_value(value)) else None
    return None  # Tolerance and fuzzy conditions do not define an identity.


def _add_secondary_group_keys(file_1_df: pd.DataFrame, file_2_df: pd.DataFrame, conditions: list[dict]) -> tuple[list[str], list[str]]:
    """Add normalized secondary-key columns used to consolidate duplicate rows."""
    group_keys: list[str] = []
    for index, condition in enumerate(conditions):
        normalize = _secondary_group_value(condition)
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
        if column.startswith("NORM_") and matcher_type in _IDENTIFIER_TYPES:
            # Identifier NORM_ columns are already compacted by
            # normalize_dataframe. Re-compacting every distinct reference is
            # both redundant and expensive on large workbooks.
            values = df[column]
            identities = values.astype(str).str.lower()
            df[identity_column] = identities.where(values.notna() & identities.ne(""), None)
        else:
            df[identity_column] = df[column].map(
                lambda value, kind=matcher_type: normalized_identity_value(value, kind)
            )
        columns.append(identity_column)
    return columns


def _numeric_rule_columns(df: pd.DataFrame, field_lists: list[list[str]]) -> list[str]:
    """Mapped fields already classified as numeric for this reconciliation."""
    fields = dict.fromkeys(field for fields in field_lists for field in fields)
    return [field for field in fields if field in df.columns]


def _columns_have_no_blanks(df: pd.DataFrame, columns: list[str]) -> bool:
    for column in columns:
        values = df[column]
        if values.isna().any():
            return False
        if values.dtype == object or pd.api.types.is_string_dtype(values.dtype):
            blank = values.astype(str).str.strip().str.lower().isin({"", "nan", "none", "nat"})
            if blank.any():
                return False
    return True


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
            source_column = condition["source_column"]
            destination_column = condition["destination_column"]
            matcher_type = condition.get("matcher_type") or detect_key_matcher_type(
                [source_value, destination_value], source_column, destination_column
            )
            source_identity = normalized_key_identity(source_value, source_column, destination_column, matcher_type)
            destination_identity = normalized_key_identity(destination_value, source_column, destination_column, matcher_type)
            passed = source_identity is not None and source_identity == destination_identity
        elif method == "normalized_date":
            passed = parse_date_value(source_value) is not None and parse_date_value(source_value) == parse_date_value(destination_value)
        elif method == "numeric_tolerance":
            left, right = to_number(source_value), to_number(destination_value)
            passed = left is not None and right is not None and abs(left - right) <= float(condition.get("numeric_tolerance") or 0)
        elif method == "matcher_based":
            source_tokens = set(tokens(source_value, use_synonyms=True))
            destination_tokens = set(tokens(destination_value, use_synonyms=True))
            passed = bool(
                source_tokens.intersection(destination_tokens)
                and compare_values(source_value, destination_value, "", condition["destination_column"]).matched
            )
        else:
            passed = False
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
    engine_mode: str | None = None,
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
    engine_mode = str(engine_mode if engine_mode is not None else os.getenv("ENGINE", "rows")).strip().lower()
    if engine_mode not in _ENGINE_VERSIONS:
        raise ValueError("Reconciliation engine must be either 'rows' or 'columns'.")
    with use_date_convention(date_dayfirst), use_normalizer(normalizer):
        return _run_generic_reconciliation(
            file_1_df, file_2_df, output_path, key_file_1, key_file_2, rules,
            include_columns_file_1, include_columns_file_2, progress_callback, file_1_name, file_2_name,
            is_cancelled, write_report, secondary_conditions, similarity_policy, date_only_override,
            transformations, matching_passes, normalizer, date_dayfirst, evaluator, learning, tolerances,
            engine_mode,
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
    normalizer: EntityNormalizer,
    date_dayfirst: bool,
    evaluator: Evaluator | None = None,
    learning: LearningContext | None = None,
    tolerances: list[dict] | None = None,
    engine_mode: str = "rows",
) -> dict:
    request = _GenericReconciliationRequest(
        file_1_df=file_1_df,
        file_2_df=file_2_df,
        output_path=output_path,
        key_file_1=key_file_1,
        key_file_2=key_file_2,
        rules=rules,
        include_columns_file_1=include_columns_file_1,
        include_columns_file_2=include_columns_file_2,
        progress_callback=progress_callback,
        file_1_name=file_1_name,
        file_2_name=file_2_name,
        is_cancelled=is_cancelled,
        write_report=write_report,
        secondary_conditions=secondary_conditions,
        similarity_policy=similarity_policy,
        date_only_override=date_only_override,
        transformations=transformations,
        matching_passes=matching_passes,
        normalizer=normalizer,
        date_dayfirst=date_dayfirst,
        evaluator=evaluator,
        learning=learning,
        tolerances=tolerances,
        engine_mode=engine_mode,
    )
    prepared = _prepare_generic_reconciliation(request)
    matches = _run_key_pass(prepared)
    _run_keyless_passes(prepared, matches)
    return _build_generic_results(prepared, matches)


def _prepare_generic_reconciliation(
    request: _GenericReconciliationRequest,
) -> _PreparedGenericReconciliation:
    file_1_df = request.file_1_df
    file_2_df = request.file_2_df
    key_file_1 = request.key_file_1
    key_file_2 = request.key_file_2
    rules = request.rules
    include_columns_file_1 = request.include_columns_file_1
    include_columns_file_2 = request.include_columns_file_2
    progress_callback = request.progress_callback
    is_cancelled = request.is_cancelled
    secondary_conditions = request.secondary_conditions
    date_only_override = request.date_only_override
    transformations = request.transformations
    matching_passes = request.matching_passes
    tolerances = request.tolerances
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

    normalized_rules: list[tuple[list[str], list[str]]] = []
    comparison_type_overrides: list[str | None] = []
    decimal_precision_overrides: list[int | None] = []
    number_format_overrides_1: list[str | None] = []
    number_format_overrides_2: list[str | None] = []
    for rule in rules:
        left = normalize_fields(rule.get("file_1_fields", []))
        right = normalize_fields(rule.get("file_2_fields", []))
        if left and right:
            normalized_rules.append((left, right))
            comparison_type_overrides.append(rule.get("comparison_type"))
            decimal_precision_overrides.append(rule.get("decimal_precision"))
            number_format_overrides_1.append(rule.get("number_format_file_1"))
            number_format_overrides_2.append(rule.get("number_format_file_2"))
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
    _infer_secondary_key_types(file_1_df, file_2_df, normalized_secondary_conditions)
    for left, right in normalized_rules:
        validate_columns(file_1_df, left, "File 1 rule")
        validate_columns(file_2_df, right, "File 2 rule")
    for config in passes:
        validate_columns(file_1_df, [config[f] for f in ("amount_source", "date_source", "narrative_source") if config.get(f)], "File 1 matching pass")
        validate_columns(file_2_df, [config[f] for f in ("amount_destination", "date_destination", "narrative_destination") if config.get(f)], "File 2 matching pass")
    combination_types = infer_combined_column_types(file_1_df, file_2_df, normalized_rules)

    comparison_types: list[str] = []
    for (left, right), override, (combination_type_1, combination_type_2) in zip(
        normalized_rules, comparison_type_overrides, combination_types
    ):
        if override:
            comparison_types.append(override)
        elif combination_type_1 or combination_type_2:
            sample = []
            for frame, fields, combination_type in (
                (file_1_df, left, combination_type_1),
                (file_2_df, right, combination_type_2),
            ):
                sample.extend(
                    collect_rule_value(row, fields, combination_type=combination_type)[0]
                    for row in frame[fields].head(100).to_dict("records")
                )
            if "text" in (combination_type_1, combination_type_2):
                header_type = detect_matcher_type([], fields_label(left), fields_label(right))
                comparison_types.append(
                    header_type
                    if header_type in {"company_name", "person_name", "invoice", "gstin", "pan", "identifier"}
                    else "text"
                )
            else:
                comparison_types.append(detect_compared_field_type(sample, fields_label(left), fields_label(right)))
        else:
            sample = (
                non_empty_values(file_1_df[left[0]].head(100))
                + non_empty_values(file_2_df[right[0]].head(100))
            )
            comparison_types.append(detect_compared_field_type(sample, left[0], right[0]))

    comparison_precisions: list[int | None] = []
    precision_metadata: list[dict] = []
    number_formats_1: list[str | None] = []
    number_formats_2: list[str | None] = []
    number_format_metadata: list[dict] = []
    for (left, right), comparison_type, precision_override, format_override_1, format_override_2 in zip(
        normalized_rules,
        comparison_types,
        decimal_precision_overrides,
        number_format_overrides_1,
        number_format_overrides_2,
    ):
        if comparison_type != "numeric":
            comparison_precisions.append(None)
            number_formats_1.append(None)
            number_formats_2.append(None)
            continue
        detected_format_1, format_source_1 = detect_number_format(
            value for column in left for value in file_1_df[column]
        )
        detected_format_2, format_source_2 = detect_number_format(
            value for column in right for value in file_2_df[column]
        )
        number_format_1 = format_override_1 or detected_format_1
        number_format_2 = format_override_2 or detected_format_2
        number_formats_1.append(number_format_1)
        number_formats_2.append(number_format_2)
        number_format_metadata.append({
            "file_1_fields": left,
            "file_2_fields": right,
            "file_1_format": number_format_1,
            "file_1_source": "configured" if format_override_1 else format_source_1,
            "file_2_format": number_format_2,
            "file_2_source": "configured" if format_override_2 else format_source_2,
        })
        if precision_override is not None and not 0 <= int(precision_override) <= 12:
            raise ValueError("Decimal precision must be between 0 and 12 places.")
        precision = (
            int(precision_override)
            if precision_override is not None
            else infer_decimal_precision(
                to_decimal(value, number_format)
                for frame, columns, number_format in (
                    (file_1_df, left, number_format_1),
                    (file_2_df, right, number_format_2),
                )
                for column in columns
                for value in frame[column]
            )
        )
        comparison_precisions.append(precision)
        precision_metadata.append({
            "file_1_fields": left,
            "file_2_fields": right,
            "decimal_places": precision,
            "source": "configured" if precision_override is not None else "detected",
        })

    # Key types come from the raw values. Name/text keys keep their raw values
    # for matching, so business normalization can see word boundaries;
    # identifier keys are compacted (INV-005 == INV/005) as before.
    raw_key_types = [
        detect_primary_key_type(
            non_empty_values(list(file_1_df[source].head(500)) + list(file_2_df[destination].head(500)), limit=200),
            source,
            destination,
        )
        for source, destination in zip(file_1_id_col, file_2_id_col)
    ]
    text_keys_1 = {column for column, kind in zip(file_1_id_col, raw_key_types) if kind in _TEXT_LIKE_TYPES}
    text_keys_2 = {column for column, kind in zip(file_2_id_col, raw_key_types) if kind in _TEXT_LIKE_TYPES}
    # How strictly reference numbers must agree (docs/adr/0002). At "exact",
    # identifier keys keep their raw values and ignore only case and spaces,
    # so INV-005 and INV/005 no longer pair.
    reference_level_name = reference_level(request.similarity_policy)
    exact_reference_positions = {
        position for position, kind in enumerate(raw_key_types)
        if reference_level_name == "exact" and kind in _IDENTIFIER_TYPES
    }
    raw_keys_1 = text_keys_1 | {file_1_id_col[position] for position in exact_reference_positions}
    raw_keys_2 = text_keys_2 | {file_2_id_col[position] for position in exact_reference_positions}

    # Only primary-key normalization columns participate in matching. Mapped
    # fields are compared directly by their specialized matcher, so creating
    # NORM_ copies for every mapped date, number, and name performs a full
    # extra pass over large workbooks without affecting a decision or report.
    # Date primary keys still receive their canonical key form.
    key_date_columns_1 = [key for key in file_1_id_col if "date" in key.lower()]
    key_date_columns_2 = [key for key in file_2_id_col if "date" in key.lower()]
    f1_norm_config = {
        "date_columns": key_date_columns_1,
        "text_columns": [],
        "number_columns": [],
        "id_columns": [key for key in file_1_id_col if key not in raw_keys_1],
    }
    f2_norm_config = {
        "date_columns": key_date_columns_2,
        "text_columns": [],
        "number_columns": [],
        "id_columns": [key for key in file_2_id_col if key not in raw_keys_2],
    }

    file_1_df = normalize_dataframe(file_1_df, f1_norm_config)
    file_2_df = normalize_dataframe(file_2_df, f2_norm_config)

    # Use normalized keys for matching (raw values for name/text keys).
    file_1_match_keys = [
        k if k in raw_keys_1 else (f"NORM_{k}" if f"NORM_{k}" in file_1_df.columns else k) for k in file_1_id_col
    ]
    file_2_match_keys = [
        k if k in raw_keys_2 else (f"NORM_{k}" if f"NORM_{k}" in file_2_df.columns else k) for k in file_2_id_col
    ]

    key_matcher_types = [
        detect_primary_key_type(
            non_empty_values(
                [*file_1_df[source_key].head(500), *file_2_df[destination_key].head(500)],
                limit=200,
            ),
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
    key_matcher_types = [
        "exact_reference" if position in exact_reference_positions else kind
        for position, kind in enumerate(key_matcher_types)
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
        numeric_fields_1 = [left for (left, _), kind in zip(normalized_rules, comparison_types) if kind == "numeric"]
        numeric_fields_2 = [right for (_, right), kind in zip(normalized_rules, comparison_types) if kind == "numeric"]
        file_1_df = consolidate_duplicate_keys(
            file_1_df,
            identity_1 + group_keys_1,
            _numeric_rule_columns(file_1_df, numeric_fields_1),
            "File 1",
        )
        file_2_df = consolidate_duplicate_keys(
            file_2_df,
            identity_2 + group_keys_2,
            _numeric_rule_columns(file_2_df, numeric_fields_2),
            "File 2",
        )

    if is_cancelled and is_cancelled():
        raise InterruptedError("Reconciliation cancelled by user")

    tracker.building_indexes()

    if has_keys and len(key_matcher_types) == 1 and key_matcher_types[0] == "date" and not date_only_override:
        raise ValueError(
            "Date-only matching can be ambiguous because multiple transactions may occur on the same date. "
            "Select at least one additional identifying column or explicitly enable the date-only override."
        )
    if request.engine_mode == "columns":
        indexed_matcher = None
        # The exact column path can work directly from the frames. Delay
        # row-dictionary materialization until a complex path actually needs
        # it, instead of paying for a million destination dictionaries up
        # front.
        file_2_records = None
    else:
        indexed_matcher = (
            IndexedCandidateMatcher(file_2_df, file_2_match_keys, key_matcher_types, build_fuzzy_indexes=False)
            if has_keys else None
        )
        file_2_records = indexed_matcher.rows if indexed_matcher is not None else file_2_df.to_dict("records")
    is_composite_key = len(file_1_match_keys) > 1
    primary_key_1 = file_1_match_keys[0] if has_keys else None
    primary_key_2 = file_2_match_keys[0] if has_keys else None

    secondary_names = ", ".join(condition["source_column"] for condition in normalized_secondary_conditions)
    secondary_columns_1 = [condition["source_column"] for condition in normalized_secondary_conditions]
    secondary_columns_2 = [condition["destination_column"] for condition in normalized_secondary_conditions]
    pass_columns_1 = [config[f] for config in passes for f in ("amount_source", "date_source", "narrative_source") if config.get(f)]
    pass_columns_2 = [config[f] for config in passes for f in ("amount_destination", "date_destination", "narrative_destination") if config.get(f)]
    display_key_columns_1 = file_1_id_col or list(dict.fromkeys(pass_columns_1))
    display_key_columns_2 = file_2_id_col or list(dict.fromkeys(pass_columns_2))
    display_keys_nonblank_1 = (
        request.engine_mode == "columns" and _columns_have_no_blanks(file_1_df, display_key_columns_1)
    )
    display_keys_nonblank_2 = (
        request.engine_mode == "columns" and _columns_have_no_blanks(file_2_df, display_key_columns_2)
    )
    allowed_f1_cols = set(file_1_match_keys + file_1_id_col + secondary_columns_1 + pass_columns_1 + [r[0][i] for r in normalized_rules for i in range(len(r[0]))] + file_1_extra)
    allowed_f2_cols = set(file_2_match_keys + file_2_id_col + secondary_columns_2 + pass_columns_2 + [r[1][i] for r in normalized_rules for i in range(len(r[1]))] + file_2_extra)

    return _PreparedGenericReconciliation(
        request=request,
        tracker=tracker,
        transformed_1=transformed_1,
        transformed_2=transformed_2,
        file_1_df=file_1_df,
        file_2_df=file_2_df,
        file_1_id_col=file_1_id_col,
        file_2_id_col=file_2_id_col,
        passes=passes,
        has_keys=has_keys,
        normalized_rules=normalized_rules,
        combination_types=combination_types,
        file_1_extra=file_1_extra,
        file_2_extra=file_2_extra,
        normalized_secondary_conditions=normalized_secondary_conditions,
        compared_labels=compared_labels,
        tolerance_bands=tolerance_bands,
        comparison_types=comparison_types,
        comparison_precisions=comparison_precisions,
        precision_metadata=precision_metadata,
        number_formats_1=number_formats_1,
        number_formats_2=number_formats_2,
        number_format_metadata=number_format_metadata,
        raw_key_types=raw_key_types,
        file_1_match_keys=file_1_match_keys,
        file_2_match_keys=file_2_match_keys,
        key_matcher_types=key_matcher_types,
        indexed_matcher=indexed_matcher,
        file_2_records=file_2_records,
        is_composite_key=is_composite_key,
        primary_key_1=primary_key_1,
        primary_key_2=primary_key_2,
        secondary_names=secondary_names,
        secondary_columns_1=secondary_columns_1,
        secondary_columns_2=secondary_columns_2,
        pass_columns_1=pass_columns_1,
        pass_columns_2=pass_columns_2,
        display_key_columns_1=display_key_columns_1,
        display_key_columns_2=display_key_columns_2,
        display_keys_nonblank_1=display_keys_nonblank_1,
        display_keys_nonblank_2=display_keys_nonblank_2,
        allowed_f1_cols=allowed_f1_cols,
        allowed_f2_cols=allowed_f2_cols,
        reference_level=reference_level_name,
    )


def _destination_records(prepared: _PreparedGenericReconciliation) -> list[dict[str, Any]]:
    """Materialize destination rows only for the row-oriented fallback path."""
    if prepared.file_2_records is None:
        prepared.file_2_records = prepared.file_2_df.to_dict("records")
    return prepared.file_2_records


def _column_exact_path_is_safe(prepared: _PreparedGenericReconciliation) -> bool:
    """Whether the all-column shortcut can preserve every row-engine detail.

    Complex matching, transformations, grouped rows and auto-resolution still
    take the established general path. This deliberately narrow gate lets the
    common one-key, one-to-one case avoid millions of temporary dictionaries
    without widening the behavioral surface of the optimization.
    """
    request = prepared.request
    if request.engine_mode != "columns" or not prepared.has_keys:
        return False
    if prepared.reference_level in {"variants", "suggestions"} and any(
        kind in VARIANT_KEY_TYPES for kind in prepared.raw_key_types
    ):
        return False
    if len(prepared.file_1_id_col) != 1 or len(prepared.file_2_id_col) != 1:
        return False
    if prepared.normalized_secondary_conditions or prepared.passes:
        return False
    if prepared.file_1_extra or prepared.file_2_extra:
        return False
    if request.evaluator is not None or request.learning is not None:
        return False
    if (
        prepared.transformed_1.applied
        or prepared.transformed_2.applied
        or prepared.transformed_1.originals
        or prepared.transformed_2.originals
    ):
        return False
    if any(source_type or destination_type for source_type, destination_type in prepared.combination_types):
        return False
    supported_types = {"text", "person_name", "company_name", "numeric", "invoice", "gstin", "pan", "identifier", "date"}
    if any(comparison_type not in supported_types for comparison_type in prepared.comparison_types):
        return False
    return True


def _column_key_normalization_rules(
    prepared: _PreparedGenericReconciliation,
    source_positions: np.ndarray,
    destination_positions: np.ndarray,
) -> dict[int, tuple[str, ...]]:
    """Keep primary-key normalization evidence without building source records.

    Exact column pairing happens on the engine's normalized key form. A small
    subset can therefore have different raw spellings (for example ``UTR-1``
    and ``UTR1``). The row engine exposes that fact in its audit trail, so the
    fast path calculates the same evidence only for those non-identical pairs.
    """
    if not len(source_positions):
        return {}
    source_column, destination_column = prepared.file_1_id_col[0], prepared.file_2_id_col[0]
    matcher_type = prepared.raw_key_types[0]
    source_values = prepared.file_1_df[source_column].iloc[source_positions].tolist()
    destination_values = prepared.file_2_df[destination_column].iloc[destination_positions].tolist()
    applied_by_pair: dict[int, tuple[str, ...]] = {}
    for pair_index, (source_value, destination_value) in enumerate(zip(source_values, destination_values)):
        identical = False
        if type(source_value) is not type(destination_value):
            identical = False
        else:
            try:
                identical = bool(source_value == destination_value)
            except (TypeError, ValueError):
                identical = False
        if identical:
            continue
        rules: list[str] = []
        if matcher_type in _TEXT_LIKE_TYPES:
            rules.extend(prepared.request.normalizer.explain(source_value, destination_value))
        elif matcher_type in _IDENTIFIER_TYPES and _identifier_formatting_matches(
            source_value,
            destination_value,
            source_column,
            destination_column,
            matcher_type,
        ):
            rules.append(_identifier_rule_note(prepared.reference_level))
        if rules:
            applied_by_pair[pair_index] = tuple(dict.fromkeys(rules))
    return applied_by_pair


def _column_batches_cover_all_pairs(
    prepared: _PreparedGenericReconciliation,
    source_positions: np.ndarray,
    text_batches: dict[int, TextPairComparison],
    numeric_batches: dict[int, NumericPairComparison],
    identifier_batches: dict[int, IdentifierPairComparison],
    date_batches: dict[int, DatePairComparison],
) -> bool:
    """Keep malformed or mixed cells on the general path, never guess here."""
    if not len(source_positions):
        return True
    for index, comparison_type in enumerate(prepared.comparison_types):
        if comparison_type in {"text", "person_name", "company_name"}:
            if index not in text_batches:
                return False
        elif comparison_type == "numeric":
            batch = numeric_batches.get(index)
            if batch is None or not bool(batch.valid.all()):
                return False
        elif comparison_type in {"invoice", "gstin", "pan", "identifier"}:
            batch = identifier_batches.get(index)
            if batch is None or not bool(batch.valid.all()):
                return False
        elif comparison_type == "date":
            batch = date_batches.get(index)
            if batch is None or not bool(batch.valid.all()):
                return False
        else:
            return False
    return True


def _try_column_exact_match_state(prepared: _PreparedGenericReconciliation) -> _GenericMatchState | None:
    """Build compact state for straightforward exact-key column reconciliation."""
    if not _column_exact_path_is_safe(prepared):
        return None
    identity_columns_1 = ["__KEY_0__"]
    identity_columns_2 = ["__KEY_0__"]
    source_positions, destination_positions = unique_exact_pair_positions(
        prepared.file_1_df,
        prepared.file_2_df,
        identity_columns_1,
        identity_columns_2,
    )
    key_normalization_rules = _column_key_normalization_rules(
        prepared,
        source_positions,
        destination_positions,
    )
    text_batches, numeric_batches, identifier_batches, date_batches = _prepare_column_comparisons_for_positions(
        prepared,
        source_positions,
        destination_positions,
    )
    if not _column_batches_cover_all_pairs(
        prepared,
        source_positions,
        text_batches,
        numeric_batches,
        identifier_batches,
        date_batches,
    ):
        return None

    destination_by_source = np.full(len(prepared.file_1_df), -1, dtype=np.int64)
    destination_by_source[source_positions] = destination_positions
    destination_matched = np.zeros(len(prepared.file_2_df), dtype=bool)
    destination_matched[destination_positions] = True
    return _GenericMatchState(
        outcomes=None,
        matched_file_2_indices=None,
        column_exact_pairs=_ColumnExactMatchState(
            source_positions=source_positions,
            destination_positions=destination_positions,
            destination_by_source=destination_by_source,
            destination_matched=destination_matched,
            key_normalization_rules=key_normalization_rules,
            text_batches=text_batches,
            numeric_batches=numeric_batches,
            identifier_batches=identifier_batches,
            date_batches=date_batches,
        ),
    )


def _run_key_pass(prepared: _PreparedGenericReconciliation) -> _GenericMatchState:
    request = prepared.request
    tracker = prepared.tracker
    file_1_df = prepared.file_1_df
    has_keys = prepared.has_keys
    file_1_match_keys = prepared.file_1_match_keys
    is_composite_key = prepared.is_composite_key
    primary_key_1 = prepared.primary_key_1
    indexed_matcher = prepared.indexed_matcher
    normalized_secondary_conditions = prepared.normalized_secondary_conditions
    file_1_id_col = prepared.file_1_id_col
    file_2_id_col = prepared.file_2_id_col
    key_matcher_types = prepared.key_matcher_types
    is_cancelled = request.is_cancelled
    learning = request.learning
    similarity_policy = request.similarity_policy
    decision_keys = prepared.decision_keys
    rejected_note = prepared.rejected_note
    tracker.matching_records()
    column_exact_state = _try_column_exact_match_state(prepared)
    if column_exact_state is not None:
        return column_exact_state

    _destination_records(prepared)
    # ── Pass 1: primary key (and mandatory secondary keys) ────────────────
    file_1_records = file_1_df.to_dict('records')
    outcomes: list[dict] = [
        {
            "row_idx": row_idx, "row": row, "classification": MatchClassification.NOT_FOUND,
            "key_result": None, "threshold": None, "best_idx": None, "file_2_row": None, "candidate_row": None,
            "secondary_mismatch": [], "pass_label": "", "explanation": None, "method": "", "learned_note": "",
            "key_partners": {},
        }
        for row_idx, row in enumerate(file_1_records)
    ]
    matched_file_2_indices: set[object] = set()
    next_cancel_check: float | None = None
    fallback_positions = set(range(len(outcomes)))

    if request.engine_mode == "columns" and has_keys:
        identity_columns_1 = [f"__KEY_{index}__" for index in range(len(file_1_match_keys))]
        identity_columns_2 = [f"__KEY_{index}__" for index in range(len(prepared.file_2_match_keys))]
        destination_indices = list(prepared.file_2_df.index)
        source_positions, destination_positions = unique_exact_pair_positions(
            file_1_df, prepared.file_2_df, identity_columns_1, identity_columns_2
        )
        for source_position, destination_position in zip(source_positions, destination_positions):
            source_position, destination_position = int(source_position), int(destination_position)
            file_1_row = file_1_records[source_position]
            file_2_row = prepared.file_2_records[destination_position]
            if _failed_secondary_conditions(file_1_row, file_2_row, normalized_secondary_conditions):
                continue

            if is_composite_key:
                identity = composite_identity_key(
                    [file_1_row.get(column) for column in file_1_match_keys], key_matcher_types
                )
                if identity is None:
                    continue
                normalized = " | ".join(identity)
                key_result = MatchResult(
                    True, 100, "composite", "Exact composite key match",
                    f"Exact normalized match across {len(file_1_match_keys)} primary-key columns",
                    normalized, normalized,
                )
            else:
                identity = normalized_identity_value(
                    file_1_row.get(file_1_match_keys[0]), key_matcher_types[0]
                )
                if identity is None:
                    continue
                key_result = MatchResult(
                    True, 100, key_matcher_types[0], "Exact normalized primary-key match",
                    value1_normalized=identity, value2_normalized=identity,
                )

            destination_index = destination_indices[destination_position]
            outcomes[source_position].update(
                classification=MatchClassification.EXACT_MATCH,
                key_result=key_result,
                best_idx=destination_index,
                file_2_row=file_2_row,
                candidate_row=file_2_row,
                pass_label="Primary key",
            )
            matched_file_2_indices.add(destination_index)
            fallback_positions.discard(source_position)

        if not normalized_secondary_conditions:
            # With no secondary rule, preparation has already consolidated every
            # complete duplicate key. Non-joined rows are unmatched, not fuzzy candidates.
            fallback_positions.clear()

        if fallback_positions and indexed_matcher is None:
            indexed_matcher = IndexedCandidateMatcher(
                prepared.file_2_df,
                prepared.file_2_match_keys,
                prepared.key_matcher_types,
                build_fuzzy_indexes=False,
            )
            prepared.indexed_matcher = indexed_matcher

    for row_idx in sorted(fallback_positions):
        file_1_row = file_1_records[row_idx]
        if is_cancelled and row_idx % 25 == 0:
            now = time.monotonic()
            if next_cancel_check is None or now >= next_cancel_check:
                next_cancel_check = now + 1.0
                if is_cancelled():
                    raise InterruptedError("Reconciliation cancelled by user")

        outcome = outcomes[row_idx]
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
            rejected = exact_candidates
            exact_candidates = [
                candidate for candidate in exact_candidates
                if indexed_matcher.matches_conditions(file_1_row, candidate[1], normalized_secondary_conditions)
            ]
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
            # Similar-key proposals are deferred until every exact key has
            # been reserved. Otherwise an earlier fuzzy row can steal the
            # only destination row belonging to a later exact row.
            pass

        if file_2_row is not None:
            matched_file_2_indices.add(best_idx)
        outcome.update(
            classification=classification, key_result=key_result, threshold=required_threshold, best_idx=best_idx,
            file_2_row=file_2_row, candidate_row=candidate_row, secondary_mismatch=secondary_mismatch,
            pass_label="Primary key" if file_2_row is not None else "",
        )

    # ── Pass 1a: the same reference written differently (docs/adr/0002) ──
    _run_reference_variant_passes(prepared, outcomes, matched_file_2_indices)

    # ── Pass 1b: similar primary keys after all exact matches are reserved ──
    if has_keys and normalized_secondary_conditions:
        for row_idx in sorted(fallback_positions):
            outcome = outcomes[row_idx]
            if row_idx % 25 == 0:
                now = time.monotonic()
                if next_cancel_check is None or now >= next_cancel_check:
                    next_cancel_check = now + 1.0
                    if is_cancelled and is_cancelled():
                        raise InterruptedError("Reconciliation cancelled by user")
            if outcome["classification"] is not MatchClassification.NOT_FOUND or outcome["secondary_mismatch"]:
                continue

            file_1_row = outcome["row"]
            file_1_id = (
                [file_1_row.get(key) for key in file_1_match_keys]
                if is_composite_key
                else file_1_row.get(primary_key_1)
            )
            classification = MatchClassification.NOT_FOUND
            key_result: MatchResult | None = None
            required_threshold: int | None = None
            best_idx = None
            file_2_row = None
            candidate_row = None
            method = ""
            secondary_candidates = indexed_matcher.bounded_secondary_candidates(
                file_1_row,
                normalized_secondary_conditions,
                matched_file_2_indices,
                _MAX_DIRECT_SIMILARITY_CANDIDATES,
                file_1_id,
            )
            # Secondary keys alone never identify a record: a candidate must also
            # have a similar primary key. Large candidate sets are narrowed by
            # the primary-key token index inside the indexed matcher.
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
            if len(qualifying) == 1:
                method = f"Similar key ({key_result.matcher_type.replace('_', ' ')})"

            if file_2_row is not None:
                matched_file_2_indices.add(best_idx)
            outcome.update(
                classification=classification, key_result=key_result, threshold=required_threshold, best_idx=best_idx,
                file_2_row=file_2_row, candidate_row=candidate_row, method=method,
            )

    # ── Pass 1c: review-only suggestions for similar references ──────────
    _run_reference_suggestions(prepared, outcomes, matched_file_2_indices)

    return _GenericMatchState(outcomes, matched_file_2_indices)


def _open_key_outcomes(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Records the key pass left unmatched without a decision for a person.

    Ambiguous records and same-key, different-secondary-key records are never
    re-paired by a weaker rule.
    """
    return [
        outcome for outcome in outcomes
        if outcome["file_2_row"] is None
        and outcome["classification"] is MatchClassification.NOT_FOUND
        and not outcome["secondary_mismatch"]
    ]


def _run_reference_variant_passes(
    prepared: _PreparedGenericReconciliation,
    outcomes: list[dict[str, Any]],
    matched_file_2_indices: set[object],
) -> None:
    """Pair reference numbers written differently (docs/adr/0002).

    Rules run in order on records the exact key did not match: leading zeros
    (a match), then look-alike characters and a document prefix or financial
    year (both for review). A rule pairs a record only with the one remaining
    candidate that also passes every "must also match" column, and only when
    no other open record qualifies for that candidate.
    """
    if prepared.reference_level not in {"variants", "suggestions"} or not prepared.has_keys:
        return
    variant_components = [kind in VARIANT_KEY_TYPES for kind in prepared.raw_key_types]
    if not any(variant_components):
        return
    destination_rows = _destination_records(prepared)
    destination_indices = list(prepared.file_2_df.index)
    key_count = len(prepared.file_1_id_col)
    conditions = prepared.normalized_secondary_conditions
    learning = prepared.request.learning
    result_type = "composite" if prepared.is_composite_key else prepared.key_matcher_types[0]

    def form(rule, row: dict[str, Any], columns: list[str]):
        return key_forms(
            rule,
            [row.get(column) for column in columns],
            variant_components,
            [row.get(f"__KEY_{position}__") for position in range(key_count)],
        )

    for rule in VARIANT_RULES:
        sources = _open_key_outcomes(outcomes)
        if not sources:
            return
        destinations_by_key: dict[tuple, list[tuple[object, dict[str, Any], tuple]]] = {}
        for index, row in zip(destination_indices, destination_rows):
            if index in matched_file_2_indices:
                continue
            destination_form = form(rule, row, prepared.file_2_id_col)
            if destination_form is not None:
                destinations_by_key.setdefault(destination_form[0], []).append((index, row, destination_form))
        if not destinations_by_key:
            return
        sources_by_key: dict[tuple, list[tuple[dict[str, Any], tuple]]] = {}
        for outcome in sources:
            source_form = form(rule, outcome["row"], prepared.file_1_id_col)
            if source_form is not None and source_form[0] in destinations_by_key:
                sources_by_key.setdefault(source_form[0], []).append((outcome, source_form))

        for key, source_entries in sources_by_key.items():
            def qualifies(source_row: dict[str, Any], source_form: tuple, destination_row: dict[str, Any], destination_form: tuple) -> bool:
                # The rule must have changed a value, or the exact key would
                # already have paired the two; a financial year present on
                # both sides must be the same year.
                return (
                    (source_form[1] or destination_form[1])
                    and years_compatible(source_form[2], destination_form[2])
                    and not _failed_secondary_conditions(source_row, destination_row, conditions)
                )

            for outcome, source_form in source_entries:
                if outcome["file_2_row"] is not None or outcome["classification"] is not MatchClassification.NOT_FOUND:
                    continue
                source_row = outcome["row"]
                candidates = [
                    (index, row, destination_form)
                    for index, row, destination_form in destinations_by_key[key]
                    if index not in matched_file_2_indices and qualifies(source_row, source_form, row, destination_form)
                ]
                if not candidates:
                    continue
                rivals = [
                    other for other, other_form in source_entries
                    if other is not outcome
                    and other["file_2_row"] is None
                    and other["classification"] is MatchClassification.NOT_FOUND
                    and any(qualifies(other["row"], other_form, row, destination_form) for _, row, destination_form in candidates)
                ]
                if len(candidates) > 1 or rivals:
                    count = len(candidates) + len(rivals)
                    outcome.update(
                        classification=MatchClassification.AMBIGUOUS_MATCH,
                        method=rule.label,
                        explanation=(
                            f"{count} records share this key once {rule.explanation} are ignored, so none was chosen."
                        ),
                    )
                    continue
                index, destination_row, _ = candidates[0]
                key_1, key_2 = prepared.decision_keys(source_row, destination_row)
                if rule.needs_review and learning is not None and learning.is_rejected(key_1, key_2):
                    outcome["learned_note"] = prepared.rejected_note(source_row, [destination_row])
                    continue
                matched_file_2_indices.add(index)
                if rule.needs_review:
                    classification = MatchClassification.EXCEPTION_MATCH
                    key_result = MatchResult(True, 99, result_type, f"Key differs only by {rule.explanation}")
                    explanation = f"Key differs only by {rule.explanation} ({key_1} ↔ {key_2}). Please confirm."
                else:
                    classification = MatchClassification.EXACT_MATCH
                    key_result = MatchResult(True, 100, result_type, f"Key matched after removing {rule.explanation}")
                    explanation = f"Key matched after removing {rule.explanation} ({key_1} = {key_2})."
                outcome.update(
                    classification=classification,
                    key_result=key_result,
                    best_idx=index,
                    file_2_row=destination_row,
                    candidate_row=destination_row,
                    pass_label="Primary key",
                    method=rule.label,
                    explanation=explanation,
                )


_SUGGESTION_FIELD_TYPES = {"numeric", "date", "company_name", "person_name"}


def _suggestion_agreement(
    prepared: _PreparedGenericReconciliation,
    source_row: dict[str, Any],
    destination_row: dict[str, Any],
) -> list[str] | None:
    """Mapped amount, date and party fields that confirm a suggestion.

    Returns None when any of them disagrees beyond its tolerance, or when no
    amount is mapped: a similar reference alone never proposes a pairing.
    """
    agreed: list[str] = []
    has_amount = False
    for index, ((left, right), kind) in enumerate(zip(prepared.normalized_rules, prepared.comparison_types)):
        if kind not in _SUGGESTION_FIELD_TYPES:
            continue
        combination_1, combination_2 = prepared.combination_types[index]
        differences = compare_rule_values(
            source_row,
            destination_row,
            left,
            right,
            matcher_type_override=kind,
            decimal_precision=prepared.comparison_precisions[index],
            number_format_file_1=prepared.number_formats_1[index],
            number_format_file_2=prepared.number_formats_2[index],
            combination_type_file_1=combination_1,
            combination_type_file_2=combination_2,
        )
        label = fields_label(left)
        if differences and accepted_reason(
            label,
            differences.get(f"{label} (FILE 1)"),
            differences.get(f"{label} (FILE 2)"),
            differences.get(f"{label} DIFF"),
            prepared.tolerance_bands,
            fields_label(right),
            differences.get(f"{label} CONFIDENCE"),
        ) is None:
            return None
        has_amount = has_amount or kind == "numeric"
        agreed.append(label)
    return agreed if has_amount else None


def _run_reference_suggestions(
    prepared: _PreparedGenericReconciliation,
    outcomes: list[dict[str, Any]],
    matched_file_2_indices: set[object],
) -> None:
    """Propose similar references for review, never as matches.

    Only at the "Variants + suggestions" level, for a single reference key.
    A proposal needs a similarity of at least ``SUGGESTION_MIN_SCORE``, every
    mapped amount (and date or party, when mapped) in agreement, a lead of
    ``SUGGESTION_MIN_LEAD`` points over the next candidate, and to be the
    best proposal for its destination record too.
    """
    if prepared.reference_level != "suggestions" or not prepared.has_keys:
        return
    if len(prepared.file_1_id_col) != 1 or prepared.raw_key_types[0] not in VARIANT_KEY_TYPES:
        return
    if "numeric" not in prepared.comparison_types:
        return
    sources = _open_key_outcomes(outcomes)
    if not sources:
        return
    source_column, destination_column = prepared.file_1_id_col[0], prepared.file_2_id_col[0]
    destination_rows = {
        index: row
        for index, row in zip(prepared.file_2_df.index, _destination_records(prepared))
        if index not in matched_file_2_indices
    }
    suggester = ReferenceSuggester(
        (index, suggestion_form(row.get(destination_column))) for index, row in destination_rows.items()
    )
    conditions = prepared.normalized_secondary_conditions
    learning = prepared.request.learning

    proposals: dict[object, list[tuple[int, dict[str, Any], list[str]]]] = {}
    for outcome in sources:
        source_row = outcome["row"]
        confirmed = []
        for index, score in suggester.candidates(suggestion_form(source_row.get(source_column))):
            destination_row = destination_rows[index]
            if _failed_secondary_conditions(source_row, destination_row, conditions):
                continue
            if learning is not None and learning.is_rejected(*prepared.decision_keys(source_row, destination_row)):
                continue
            agreed = _suggestion_agreement(prepared, source_row, destination_row)
            if agreed is not None:
                confirmed.append((index, score, agreed))
        if not confirmed:
            continue
        if len(confirmed) > 1 and confirmed[0][1] - confirmed[1][1] < SUGGESTION_MIN_LEAD:
            continue
        index, score, agreed = confirmed[0]
        proposals.setdefault(index, []).append((score, outcome, agreed))

    for index, entries in proposals.items():
        entries.sort(key=lambda entry: -entry[0])
        if len(entries) > 1 and entries[0][0] - entries[1][0] < SUGGESTION_MIN_LEAD:
            continue  # Two records want this one equally: a person decides.
        score, outcome, agreed = entries[0]
        destination_row = destination_rows[index]
        key_1, key_2 = prepared.decision_keys(outcome["row"], destination_row)
        matched_file_2_indices.add(index)
        outcome.update(
            classification=MatchClassification.EXCEPTION_MATCH,
            key_result=MatchResult(True, score, prepared.key_matcher_types[0], "Similar reference"),
            threshold=SUGGESTION_MIN_SCORE,
            best_idx=index,
            file_2_row=destination_row,
            candidate_row=destination_row,
            pass_label="Primary key",
            method="Similar reference (suggestion)",
            explanation=(
                f"Suggested pairing: {key_1} and {key_2} are {score}% similar, and "
                f"{', '.join(agreed)} agree{'s' if len(agreed) == 1 else ''}. Please confirm."
            ),
        )


def _run_keyless_passes(
    prepared: _PreparedGenericReconciliation,
    match_state: _GenericMatchState,
) -> None:
    if match_state.column_exact_pairs is not None:
        return
    request = prepared.request
    file_2_df = prepared.file_2_df
    file_2_records = _destination_records(prepared)
    passes = prepared.passes
    has_keys = prepared.has_keys
    normalized_secondary_conditions = prepared.normalized_secondary_conditions
    outcomes = match_state.outcomes
    matched_file_2_indices = match_state.matched_file_2_indices
    assert outcomes is not None and matched_file_2_indices is not None
    learning = request.learning
    decision_keys = prepared.decision_keys
    rejected_note = prepared.rejected_note
    # ── Later passes: keyless matching on records still unmatched ─────────
    if passes:
        file_2_positions = {index: position for position, index in enumerate(file_2_df.index)}
        file_2_rows_all = file_2_records

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

            pass_runner = run_column_pass if request.engine_mode == "columns" else run_pass
            matches, ambiguities = pass_runner(config, open_sources, open_destinations, check)
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


def _prepare_column_comparisons(
    prepared: _PreparedGenericReconciliation,
    outcomes: list[dict[str, Any]],
) -> tuple[
    dict[int, TextPairComparison],
    dict[int, NumericPairComparison],
    dict[int, IdentifierPairComparison],
    dict[int, DatePairComparison],
    np.ndarray,
]:
    """Batch safe one-column comparisons before building result rows."""
    pair_positions = np.full(len(outcomes), -1, dtype=np.int64)
    if prepared.request.engine_mode != "columns" or not prepared.file_2_df.index.is_unique:
        return {}, {}, {}, {}, pair_positions

    matched_outcomes = [
        (outcome_index, outcome)
        for outcome_index, outcome in enumerate(outcomes)
        if outcome["file_2_row"] is not None and outcome["key_result"] is not None
    ]
    if not matched_outcomes:
        return {}, {}, {}, {}, pair_positions

    destination_positions = prepared.file_2_df.index.get_indexer(
        [outcome["best_idx"] for _, outcome in matched_outcomes]
    )
    if (destination_positions < 0).any():
        return {}, {}, {}, {}, pair_positions

    source_positions = [outcome["row_idx"] for _, outcome in matched_outcomes]
    pair_positions[[index for index, _ in matched_outcomes]] = np.arange(len(matched_outcomes))
    batches = _prepare_column_comparisons_for_positions(
        prepared,
        np.asarray(source_positions, dtype=np.int64),
        destination_positions.astype(np.int64, copy=False),
    )
    return *batches, pair_positions


def _prepare_column_comparisons_for_positions(
    prepared: _PreparedGenericReconciliation,
    source_positions: np.ndarray,
    destination_positions: np.ndarray,
) -> tuple[
    dict[int, TextPairComparison],
    dict[int, NumericPairComparison],
    dict[int, IdentifierPairComparison],
    dict[int, DatePairComparison],
]:
    """Prepare supported column batches for aligned source/destination rows."""
    if not len(source_positions):
        return {}, {}, {}, {}
    batches: dict[int, TextPairComparison] = {}
    numeric_batches: dict[int, NumericPairComparison] = {}
    identifier_batches: dict[int, IdentifierPairComparison] = {}
    date_batches: dict[int, DatePairComparison] = {}
    text_types = {"text", "person_name", "company_name"}
    identifier_types = {"invoice", "gstin", "pan", "identifier"}
    for rule_index, (
        (fields_1, fields_2), comparison_type, combination_types,
        decimal_precision, number_format_1, number_format_2,
    ) in enumerate(zip(
        prepared.normalized_rules,
        prepared.comparison_types,
        prepared.combination_types,
        prepared.comparison_precisions,
        prepared.number_formats_1,
        prepared.number_formats_2,
    )):
        if len(fields_1) != 1 or len(fields_2) != 1 or any(combination_types):
            continue
        if fields_1[0] not in prepared.file_1_df.columns or fields_2[0] not in prepared.file_2_df.columns:
            continue
        values_1 = prepared.file_1_df[fields_1[0]].iloc[source_positions].tolist()
        values_2 = prepared.file_2_df[fields_2[0]].iloc[destination_positions].tolist()
        if comparison_type in text_types:
            batch = compare_text_pairs(values_1, values_2, prepared.request.normalizer)
            if batch is not None:
                batches[rule_index] = batch
        elif comparison_type == "numeric":
            batch = compare_numeric_pairs(
                values_1, values_2, decimal_precision, number_format_1, number_format_2
            )
            if batch is not None:
                numeric_batches[rule_index] = batch
        elif comparison_type in identifier_types:
            batch = compare_identifier_pairs(values_1, values_2)
            if batch is not None:
                identifier_batches[rule_index] = batch
        elif comparison_type == "date":
            batch = compare_date_pairs(values_1, values_2)
            if batch is not None:
                date_batches[rule_index] = batch
    return batches, numeric_batches, identifier_batches, date_batches


def _outcome_display_key(
    prepared: _PreparedGenericReconciliation,
    outcome: dict[str, Any],
    side: int,
    row: dict[str, Any],
) -> str:
    cache_key = f"_DISPLAY_KEY_{side}"
    cached = outcome.get(cache_key)
    if cached is None:
        columns = prepared.display_key_columns_1 if side == 1 else prepared.display_key_columns_2
        nonblank = prepared.display_keys_nonblank_1 if side == 1 else prepared.display_keys_nonblank_2
        cached = prepared.display_key(row, columns, nonblank)
        outcome[cache_key] = cached
    return cached


def _display_column_value(value: object, values_nonblank: bool) -> str:
    """Match the one-column display-key behavior without materializing a row."""
    if values_nonblank:
        return str(value)
    return "" if is_blank(value) else str(value)


def _python_scalar(value: object) -> object:
    """Match DataFrame ``to_dict`` values when a lazy row reads an ndarray."""
    return value.item() if isinstance(value, np.generic) else value


def _grouped_rows_note_from_values(count: object, rows: object) -> str:
    """Produce the established grouped-row note without creating a row dict."""
    try:
        group_count = int(count)
    except (TypeError, ValueError):
        group_count = 1
    return f"{group_count} rows combined (rows {rows})" if group_count > 1 else ""


def _column_exact_pair_record(
    prepared: _PreparedGenericReconciliation,
    pair_state: _ColumnExactMatchState,
    pair_index: int,
    *,
    source_row_numbers: np.ndarray,
    destination_row_numbers: np.ndarray,
    source_primary_values: np.ndarray,
    destination_primary_values: np.ndarray,
    source_raw_keys: np.ndarray,
    destination_raw_keys: np.ndarray,
    source_group_counts: np.ndarray,
    destination_group_counts: np.ndarray,
    source_group_rows: np.ndarray,
    destination_group_rows: np.ndarray,
    normalization_rules: dict[int, tuple[str, ...]],
) -> dict[str, Any]:
    """Create the established exact-match report record on demand."""
    source_position = int(pair_state.source_positions[pair_index])
    destination_position = int(pair_state.destination_positions[pair_index])
    source_raw_key = _python_scalar(source_raw_keys[source_position])
    source_grouped = int(source_group_counts[source_position]) > 1
    destination_grouped = int(destination_group_counts[destination_position]) > 1
    group_status = {
        (False, False): "One-to-One Match",
        (True, False): "Many-to-One Match",
        (False, True): "One-to-Many Match",
        (True, True): "Many-to-Many Match",
    }[(source_grouped, destination_grouped)]
    record = {
        "ROW (FILE 1)": _python_scalar(source_row_numbers[source_position]),
        "ROW (FILE 2)": _python_scalar(destination_row_numbers[destination_position]),
        "MATCH TYPE": prepared.key_matcher_types[0],
        "MATCH CONFIDENCE": "100%",
        "MATCH STATUS": "Exact normalized primary-key match",
        "IDENTITY CLASSIFICATION": MatchClassification.EXACT_MATCH.value,
        "GROUP CLASSIFICATION": group_status,
        prepared.primary_key_1: _python_scalar(source_primary_values[source_position]),
        f"MATCHED {prepared.primary_key_2}": _python_scalar(destination_primary_values[destination_position]),
        "MATCH KEY": None if is_blank(source_raw_key) else source_raw_key,
        "MATCHED KEY": _python_scalar(destination_raw_keys[destination_position]),
    }
    grouped_notes = [
        f"File 1: {note}"
        for note in (_grouped_rows_note_from_values(source_group_counts[source_position], source_group_rows[source_position]),)
        if note
    ] + [
        f"File 2: {note}"
        for note in (_grouped_rows_note_from_values(destination_group_counts[destination_position], destination_group_rows[destination_position]),)
        if note
    ]
    if grouped_notes:
        record["GROUPED ROWS"] = "; ".join(grouped_notes)
    if rules := normalization_rules.get(pair_index):
        record["NORMALIZATION APPLIED"] = ", ".join(rules)
    return record


def _build_column_exact_results(
    prepared: _PreparedGenericReconciliation,
    pair_state: _ColumnExactMatchState,
) -> dict[str, Any]:
    """Build results for the narrow all-column exact-key fast path.

    The safe-path gate guarantees one primary key, no transformations,
    secondary conditions, extra output columns, grouped records, learning, or
    resolution rules. That makes every exact pair reproducible from two row
    positions and lets fully matched records remain lazy until a report page
    or store asks for them.
    """
    request = prepared.request
    tracker = prepared.tracker
    file_1_df, file_2_df = prepared.file_1_df, prepared.file_2_df
    pair_count = len(pair_state.source_positions)
    text_types = {"text", "person_name", "company_name"}
    identifier_types = {"invoice", "gstin", "pan", "identifier"}

    # Keep normalization evidence at the same record granularity as the row
    # engine: key-only evidence belongs in identity audit rows; combined key
    # and field evidence belongs in reconciliation result rows.
    field_rules_by_pair: dict[int, list[str]] = {}
    for batch in pair_state.text_batches.values():
        for pair_index, rules in batch.normalization_rules.items():
            if not rules:
                continue
            bucket = field_rules_by_pair.setdefault(pair_index, [])
            for rule in rules:
                if rule not in bucket:
                    bucket.append(rule)

    all_rules_by_pair: dict[int, tuple[str, ...]] = {}
    normalization_counts: dict[str, int] = {}
    for pair_index in set(pair_state.key_normalization_rules).union(field_rules_by_pair):
        rules = tuple(dict.fromkeys(
            (*pair_state.key_normalization_rules.get(pair_index, ()), *field_rules_by_pair.get(pair_index, ()))
        ))
        if not rules:
            continue
        all_rules_by_pair[pair_index] = rules
        for rule in rules:
            normalization_counts[rule] = normalization_counts.get(rule, 0) + 1

    mismatch_mask = np.zeros(pair_count, dtype=bool)
    for rule_index, comparison_type in enumerate(prepared.comparison_types):
        if comparison_type in text_types:
            mismatch_mask |= ~pair_state.text_batches[rule_index].equal
        elif comparison_type == "numeric":
            mismatch_mask |= ~pair_state.numeric_batches[rule_index].equal
        elif comparison_type in identifier_types:
            mismatch_mask |= pair_state.identifier_batches[rule_index].confidence != 100
        else:  # _column_exact_path_is_safe limits the remaining kind to date.
            mismatch_mask |= ~pair_state.date_batches[rule_index].equal

    source_row_numbers = file_1_df["_ROW_NO"].to_numpy(copy=False)
    destination_row_numbers = file_2_df["_ROW_NO"].to_numpy(copy=False)
    source_primary_values = file_1_df[prepared.primary_key_1].to_numpy(copy=False)
    destination_primary_values = file_2_df[prepared.primary_key_2].to_numpy(copy=False)
    source_raw_keys = file_1_df[prepared.file_1_id_col[0]].to_numpy(copy=False)
    destination_raw_keys = file_2_df[prepared.file_2_id_col[0]].to_numpy(copy=False)
    source_group_counts = (
        file_1_df["__GROUP_COUNT__"].to_numpy(copy=False)
        if "__GROUP_COUNT__" in file_1_df.columns
        else np.ones(len(file_1_df), dtype=np.int64)
    )
    destination_group_counts = (
        file_2_df["__GROUP_COUNT__"].to_numpy(copy=False)
        if "__GROUP_COUNT__" in file_2_df.columns
        else np.ones(len(file_2_df), dtype=np.int64)
    )
    source_group_rows = (
        file_1_df["__GROUPED_ROWS__"].to_numpy(copy=False)
        if "__GROUPED_ROWS__" in file_1_df.columns
        else np.full(len(file_1_df), "", dtype=object)
    )
    destination_group_rows = (
        file_2_df["__GROUPED_ROWS__"].to_numpy(copy=False)
        if "__GROUPED_ROWS__" in file_2_df.columns
        else np.full(len(file_2_df), "", dtype=object)
    )
    rule_value_arrays = [
        (
            file_1_df[fields_1[0]].to_numpy(copy=False),
            file_2_df[fields_2[0]].to_numpy(copy=False),
        )
        for fields_1, fields_2 in prepared.normalized_rules
    ]

    def exact_record(pair_index: int) -> dict[str, Any]:
        return _column_exact_pair_record(
            prepared,
            pair_state,
            pair_index,
            source_row_numbers=source_row_numbers,
            destination_row_numbers=destination_row_numbers,
            source_primary_values=source_primary_values,
            destination_primary_values=destination_primary_values,
            source_raw_keys=source_raw_keys,
            destination_raw_keys=destination_raw_keys,
            source_group_counts=source_group_counts,
            destination_group_counts=destination_group_counts,
            source_group_rows=source_group_rows,
            destination_group_rows=destination_group_rows,
            normalization_rules=all_rules_by_pair,
        )

    # Generate detail rows only for records with a field difference. The
    # column batches retain all comparison facts, so this does not revisit the
    # per-row matcher for the large exact-match majority.
    reconciliation_results: list[dict[str, Any]] = []
    mismatch_pair_indices = np.flatnonzero(mismatch_mask)
    for offset, pair_index_value in enumerate(mismatch_pair_indices):
        if request.is_cancelled and offset % 2048 == 0 and request.is_cancelled():
            raise InterruptedError("Reconciliation cancelled by user")
        pair_index = int(pair_index_value)
        source_position = int(pair_state.source_positions[pair_index])
        destination_position = int(pair_state.destination_positions[pair_index])
        record = exact_record(pair_index)
        for rule_index, ((fields_1, fields_2), comparison_type) in enumerate(
            zip(prepared.normalized_rules, prepared.comparison_types)
        ):
            label = fields_label(fields_1)
            if comparison_type in text_types:
                batch = pair_state.text_batches[rule_index]
                if batch.equal[pair_index]:
                    continue
                score = int(batch.scores[pair_index])
                status = (
                    "Minor spelling variation" if score >= 85
                    else "Possible Match - Manual Review" if score >= 75
                    else "Mismatch"
                )
                record.update({
                    f"{label} (FILE 1)": batch.values_1[pair_index],
                    f"{label} (FILE 2)": batch.values_2[pair_index],
                    f"{label} CONFIDENCE": f"{score}%",
                    f"{label} STATUS": status,
                })
            elif comparison_type == "numeric":
                batch = pair_state.numeric_batches[rule_index]
                if batch.equal[pair_index]:
                    continue
                values_1, values_2 = rule_value_arrays[rule_index]
                record.update({
                    f"{label} (FILE 1)": _python_scalar(values_1[source_position]),
                    f"{label} (FILE 2)": _python_scalar(values_2[destination_position]),
                    f"{label} DIFF": float(batch.differences[pair_index]),
                    f"{label} STATUS": "Mismatch",
                })
            elif comparison_type in identifier_types:
                batch = pair_state.identifier_batches[rule_index]
                confidence = int(batch.confidence[pair_index])
                if confidence == 100:
                    continue
                values_1, values_2 = rule_value_arrays[rule_index]
                record.update({
                    f"{label} (FILE 1)": _python_scalar(values_1[source_position]),
                    f"{label} (FILE 2)": _python_scalar(values_2[destination_position]),
                    f"{label} CONFIDENCE": f"{confidence}%",
                    f"{label} STATUS": "Identifier formatting difference only" if confidence == 97 else "Mismatch",
                })
            else:
                batch = pair_state.date_batches[rule_index]
                if batch.equal[pair_index]:
                    continue
                values_1, values_2 = rule_value_arrays[rule_index]
                record.update({
                    f"{label} (FILE 1)": _python_scalar(values_1[source_position]),
                    f"{label} (FILE 2)": _python_scalar(values_2[destination_position]),
                    f"{label} CONFIDENCE": "0%",
                    f"{label} STATUS": "Mismatch",
                })
        reconciliation_results.append(record)

    # The lazy output factories only need pair positions and the small
    # normalization maps. Release the batch value arrays before the result is
    # handed to storage or a report writer.
    pair_state.text_batches.clear()
    pair_state.numeric_batches.clear()
    pair_state.identifier_batches.clear()
    pair_state.date_batches.clear()

    # The normal report order is fully matched records in source order,
    # followed by tolerance-cleared records. Preserve that while holding the
    # large first group as a view over dataframe columns.
    fully_matched_pair_indices = np.flatnonzero(~mismatch_mask)
    base_matched_records = GeneratedRecordRows(
        len(fully_matched_pair_indices),
        lambda index: exact_record(int(fully_matched_pair_indices[index])),
    )
    tolerance_matches = ColumnarRecordRows()
    tolerance = apply_tolerance_bands(
        reconciliation_results,
        tolerance_matches,
        prepared.tolerance_bands,
        prepared.compared_labels,
        stays_in_review=lambda record: record["IDENTITY CLASSIFICATION"] == MatchClassification.EXCEPTION_MATCH.value,
    )
    matched_records = concatenate_record_rows(base_matched_records, tolerance_matches)
    field_discrepancy_count = int(mismatch_mask.sum()) - tolerance.cleared_records
    reconciliation_record_count = len(reconciliation_results)

    tracker.comparing_columns()
    if request.is_cancelled and request.is_cancelled():
        raise InterruptedError("Reconciliation cancelled by user")

    file_1_not_found: list[dict[str, Any]] = []
    for source_position_value in np.flatnonzero(pair_state.destination_by_source < 0):
        source_position = int(source_position_value)
        row = file_1_df.iloc[source_position].to_dict()
        clean_row = {name: value for name, value in row.items() if name in prepared.allowed_f1_cols}
        clean_row["ROW (FILE 1)"] = _python_scalar(source_row_numbers[source_position])
        clean_row["IDENTITY CLASSIFICATION"] = MatchClassification.NOT_FOUND.value
        if note := _grouped_rows_note_from_values(source_group_counts[source_position], source_group_rows[source_position]):
            clean_row["GROUPED ROWS"] = note
        file_1_not_found.append(clean_row)

    file_2_not_found: list[dict[str, Any]] = []
    for destination_position_value in np.flatnonzero(~pair_state.destination_matched):
        destination_position = int(destination_position_value)
        row = file_2_df.iloc[destination_position].to_dict()
        clean_row = {name: value for name, value in row.items() if name in prepared.allowed_f2_cols}
        clean_row["ROW (FILE 2)"] = _python_scalar(destination_row_numbers[destination_position])
        if note := _grouped_rows_note_from_values(
            destination_group_counts[destination_position],
            destination_group_rows[destination_position],
        ):
            clean_row["GROUPED ROWS"] = note
        file_2_not_found.append(clean_row)

    source_display_keys = [
        _display_column_value(value, prepared.display_keys_nonblank_1) for value in source_raw_keys
    ]
    destination_display_keys = [
        _display_column_value(value, prepared.display_keys_nonblank_2) for value in destination_raw_keys
    ]

    def identity_record(source_position: int) -> dict[str, Any]:
        destination_position = int(pair_state.destination_by_source[source_position])
        if destination_position < 0:
            return {
                "ROW (FILE 1)": _python_scalar(source_row_numbers[source_position]),
                "IDENTITY CLASSIFICATION": MatchClassification.NOT_FOUND.value,
                "MATCH TYPE": "",
                "MATCH CONFIDENCE": "0%",
                "MATCH THRESHOLD": "",
                "MATCH KEY": source_display_keys[source_position],
                "CANDIDATE KEY": "",
            }
        pair_index = int(np.searchsorted(pair_state.source_positions, source_position))
        record = {
            "ROW (FILE 1)": _python_scalar(source_row_numbers[source_position]),
            "IDENTITY CLASSIFICATION": MatchClassification.EXACT_MATCH.value,
            "MATCH TYPE": prepared.key_matcher_types[0],
            "MATCH CONFIDENCE": "100%",
            "MATCH THRESHOLD": "",
            "MATCH KEY": source_display_keys[source_position],
            "CANDIDATE KEY": destination_display_keys[destination_position],
        }
        if rules := pair_state.key_normalization_rules.get(pair_index):
            record["NORMALIZATION APPLIED"] = ", ".join(rules)
        return record

    identity_resolution = GeneratedRecordRows(
        len(file_1_df),
        identity_record,
        field_counts={
            "IDENTITY CLASSIFICATION": {
                MatchClassification.EXACT_MATCH.value: pair_count,
                MatchClassification.EXCEPTION_MATCH.value: 0,
                MatchClassification.AMBIGUOUS_MATCH.value: 0,
                MatchClassification.NOT_FOUND.value: len(file_1_df) - pair_count,
            }
        },
        populated_counts={"NORMALIZATION APPLIED": len(pair_state.key_normalization_rules)},
    )

    tracker.generating_report()
    pass_counts = {"Primary key": pair_count} if pair_count else {}
    universal_data = build_universal_data_model(
        job_type="generic",
        file_1_name=request.file_1_name,
        file_2_name=request.file_2_name,
        matching_keys=prepared.file_1_id_col,
        reconciliation_results=reconciliation_results,
        file_1_not_found=file_1_not_found,
        file_2_not_found=file_2_not_found,
        matched_records=matched_records,
        total_file_1=len(file_1_df),
        total_file_2=len(file_2_df),
        identity_resolution=identity_resolution,
        secondary_keys=[],
        compared_fields=[f"{fields_label(left)} ↔ {fields_label(right)}" for left, right in prepared.normalized_rules],
        comparison_field_labels=[fields_label(left) for left, _ in prepared.normalized_rules],
        consume_reconciliation_results=True,
        extra_metadata={
            "transformations": [],
            "transformations_source": [],
            "transformations_destination": [],
            "matching_passes": ["Primary key"],
            "pass_counts": pass_counts,
            "normalization_rules_used": normalization_counts,
            "date_convention": "Day first (DD/MM/YYYY)" if request.date_dayfirst else "Month first (MM/DD/YYYY)",
            "numeric_precision": prepared.precision_metadata,
            "number_formats": prepared.number_format_metadata,
            "engine": request.engine_mode,
            "engine_version": _ENGINE_VERSIONS[request.engine_mode],
            "resolution_rules": [],
            "resolution_rules_used": {},
            "tolerance_bands": [band.describe() for band in prepared.tolerance_bands],
            "tolerance_counts": {"fields": tolerance.accepted_fields, "records": tolerance.moved_to_matched},
            EXPLANATION_METADATA_KEY: explanation_metadata(secondary_names=None),
        },
    )
    universal_data["auto_resolved"] = []
    reconciliation_results.clear()

    if request.write_report:
        generate_enterprise_report(universal_data, {}, request.output_path)

    tracker.finalized()
    identity_counts = universal_data["statistics"]["identity"]
    summary_stats = {
        "report_rows": reconciliation_record_count,
        "only_in_file_1": len(file_1_not_found),
        "only_in_file_2": len(file_2_not_found),
        "confidence_review": 0,
        "source_records": len(file_1_df),
        "destination_records": len(file_2_df),
        "matched_records": pair_count,
        "fully_matched_records": pair_count - reconciliation_record_count,
        "field_discrepancies": field_discrepancy_count,
        "exact_matches": identity_counts[MatchClassification.EXACT_MATCH.value],
        "exception_matches": identity_counts[MatchClassification.EXCEPTION_MATCH.value],
        "ambiguous_matches": identity_counts[MatchClassification.AMBIGUOUS_MATCH.value],
        "not_found_matches": identity_counts[MatchClassification.NOT_FOUND.value],
        "keyless_matches": 0,
        "normalized_matches": count_populated_field(identity_resolution, "NORMALIZATION APPLIED"),
        "auto_resolved": 0,
        "auto_resolved_only_in_file_1": 0,
        "auto_resolved_only_in_file_2": 0,
        "auto_resolved_differences": 0,
        "auto_confirmed_matches": 0,
        "within_tolerance_fields": tolerance.accepted_fields,
        "within_tolerance_records": tolerance.moved_to_matched,
    }
    return {**summary_stats, "summary": summary_stats, "universal_data": universal_data}


def _build_generic_results(
    prepared: _PreparedGenericReconciliation,
    matches: _GenericMatchState,
) -> dict[str, Any]:
    if matches.column_exact_pairs is not None:
        return _build_column_exact_results(prepared, matches.column_exact_pairs)

    request = prepared.request
    tracker = prepared.tracker
    transformed_1 = prepared.transformed_1
    transformed_2 = prepared.transformed_2
    file_1_df = prepared.file_1_df
    file_2_df = prepared.file_2_df
    file_1_id_col = prepared.file_1_id_col
    file_2_id_col = prepared.file_2_id_col
    passes = prepared.passes
    has_keys = prepared.has_keys
    normalized_rules = prepared.normalized_rules
    file_1_extra = prepared.file_1_extra
    file_2_extra = prepared.file_2_extra
    normalized_secondary_conditions = prepared.normalized_secondary_conditions
    compared_labels = prepared.compared_labels
    # Exact-text "must also match" columns can be equal only after normalization
    # (an alias, "Pvt" -> "Private") even when the raw keys are identical.
    text_conditions = any(condition.get("comparison_method") == "exact_text" for condition in normalized_secondary_conditions)
    tolerance_bands = prepared.tolerance_bands
    comparison_types = prepared.comparison_types
    comparison_precisions = prepared.comparison_precisions
    precision_metadata = prepared.precision_metadata
    number_formats_1 = prepared.number_formats_1
    number_formats_2 = prepared.number_formats_2
    number_format_metadata = prepared.number_format_metadata
    file_2_records = _destination_records(prepared)
    is_composite_key = prepared.is_composite_key
    primary_key_1 = prepared.primary_key_1
    primary_key_2 = prepared.primary_key_2
    secondary_names = prepared.secondary_names
    secondary_columns_1 = prepared.secondary_columns_1
    secondary_columns_2 = prepared.secondary_columns_2
    allowed_f1_cols = prepared.allowed_f1_cols
    allowed_f2_cols = prepared.allowed_f2_cols
    file_1_name = request.file_1_name
    file_2_name = request.file_2_name
    is_cancelled = request.is_cancelled
    write_report = request.write_report
    output_path = request.output_path
    date_dayfirst = request.date_dayfirst
    evaluator = request.evaluator
    learning = request.learning
    raw_key_types = prepared.raw_key_types
    outcomes = matches.outcomes
    matched_file_2_indices = matches.matched_file_2_indices
    assert outcomes is not None and matched_file_2_indices is not None
    (
        column_text_batches,
        column_numeric_batches,
        column_identifier_batches,
        column_date_batches,
        column_pair_positions,
    ) = _prepare_column_comparisons(prepared, outcomes)
    grouped_rows_note = prepared.grouped_rows_note
    display_key = prepared.display_key
    decision_keys = prepared.decision_keys
    key_normalization = prepared.key_normalization
    reconciliation_results: list[dict] = []
    file_1_not_found: list[dict] = []
    file_2_not_found: list[dict] = []
    # The column engine can produce millions of records. Retain its two large
    # categories by field instead of as one Python dictionary per row; the
    # reporter and Parquet store still receive ordinary dictionaries in small
    # batches when they consume them.
    compact_records = request.engine_mode == "columns"
    matched_records: list[dict] | ColumnarRecordRows = ColumnarRecordRows() if compact_records else []
    identity_resolution: list[dict] | ColumnarRecordRows = ColumnarRecordRows() if compact_records else []
    auto_resolved: list[dict] = []
    field_discrepancy_count = 0
    normalization_counts: dict[str, int] = {}
    # ── Results ────────────────────────────────────────────────────────────
    for outcome_index, outcome in enumerate(outcomes):
        row_idx, file_1_row = outcome["row_idx"], outcome["row"]
        classification = outcome["classification"]
        key_result, file_2_row, candidate_row = outcome["key_result"], outcome["file_2_row"], outcome["candidate_row"]
        best_idx = outcome["best_idx"]
        file_1_display_key = _outcome_display_key(prepared, outcome, 1, file_1_row)
        file_2_display_key = (
            _outcome_display_key(prepared, outcome, 2, file_2_row)
            if file_2_row is not None else None
        )
        candidate_display_key = (
            file_2_display_key
            if candidate_row is file_2_row and file_2_display_key is not None
            else prepared.display_key(
                candidate_row, prepared.display_key_columns_2, prepared.display_keys_nonblank_2
            )
            if candidate_row is not None else ""
        )
        # Exact raw key values cannot have triggered a normalization rule, unless
        # an exact-text condition could have. Skipping that expensive explanation
        # work matters for large, mostly identical identifier columns.
        raw_keys_identical = file_2_row is not None and not text_conditions and _raw_key_values_are_identical(
            file_1_row, file_2_row, file_1_id_col, file_2_id_col
        )
        normalization_applied = (
            key_normalization(file_1_row, file_2_row)
            if file_2_row is not None and outcome["pass_label"] == "Primary key" and not raw_keys_identical
            else []
        )
        original_classification = classification
        unmatched_note = outcome["learned_note"] if file_2_row is None and classification is MatchClassification.NOT_FOUND else ""
        deferred_explanation: dict[str, Any] = {}
        if key_result is not None and original_classification in {
            MatchClassification.EXCEPTION_MATCH,
            MatchClassification.NOT_FOUND,
        }:
            deferred_explanation["has_key_result"] = True
            deferred_explanation["match_status"] = key_result.status
        if outcome["explanation"]:
            deferred_explanation["explicit"] = outcome["explanation"]
        if outcome["secondary_mismatch"]:
            deferred_explanation["secondary_mismatch"] = outcome["secondary_mismatch"]
        if outcome["learned_note"]:
            deferred_explanation["learned_note"] = outcome["learned_note"]
        if unmatched_note:
            deferred_explanation["unmatched_note_only"] = True

        # What earlier decisions say, and whether a rule confirms the match.
        method = outcome["method"]
        review_history = learned_confidence = ""
        confirmed_by = None
        append_learning = False
        if classification is MatchClassification.EXCEPTION_MATCH and file_2_row is not None and key_result is not None:
            key_1, key_2 = file_1_display_key, file_2_display_key or ""
            if learning is not None:
                review_history, learned_confidence = learning.describe(key_1, key_2, method, key_result.confidence)
                append_learning = bool(review_history or learned_confidence)
            if evaluator is not None:
                confirmed_by = evaluator.confirmation(file_1_row, key_1, key_2, key_result.confidence, method)
            if confirmed_by is not None:
                classification = MatchClassification.EXACT_MATCH
                deferred_explanation["classification"] = original_classification.value
                deferred_explanation["auto_confirmed_by"] = confirmed_by.label()
                auto_resolved.append({
                    **resolution_fields(confirmed_by, "Match to confirm", "to_confirm"),
                    "Record": key_1, "Matched With": key_2, "How Matched": method,
                    "Match Confidence": f"{key_result.confidence}%", "ROW (FILE 1)": file_1_row.get("_ROW_NO", row_idx + 2),
                })
        if append_learning:
            deferred_explanation["append_learning"] = True

        identity_record = {
            "ROW (FILE 1)": file_1_row.get("_ROW_NO", row_idx + 2),
            "IDENTITY CLASSIFICATION": classification.value,
            "MATCH TYPE": key_result.matcher_type if key_result else "",
            "MATCH CONFIDENCE": f"{key_result.confidence}%" if key_result else "0%",
            "MATCH THRESHOLD": f"{outcome['threshold']}%" if outcome["threshold"] is not None else "",
            # Raw key values make every audit row traceable without row lookups.
            "MATCH KEY": file_1_display_key,
            "CANDIDATE KEY": candidate_display_key,
        }
        if deferred_explanation:
            identity_record[EXPLANATION_CONTEXT_KEY] = deferred_explanation
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
                if compact_records:
                    outcome["row"] = outcome["file_2_row"] = outcome["candidate_row"] = None
                continue
            clean_f1_row["IDENTITY CLASSIFICATION"] = classification.value
            if deferred_explanation:
                clean_f1_row[EXPLANATION_CONTEXT_KEY] = dict(deferred_explanation)
            if note := grouped_rows_note(file_1_row):
                clean_f1_row["GROUPED ROWS"] = note
            file_1_not_found.append(clean_f1_row)
            if compact_records:
                outcome["row"] = outcome["file_2_row"] = outcome["candidate_row"] = None
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
            "GROUP CLASSIFICATION": group_status,
        }
        if passes:
            reconciliation_result["MATCH PASS"] = outcome["pass_label"]
        reconciliation_result.update(learned_fields)
        if not has_keys:
            reconciliation_result["MATCH KEY"] = file_1_display_key
            reconciliation_result["MATCHED KEY"] = file_2_display_key or ""
        elif is_composite_key:
            reconciliation_result["COMPOSITE MATCH KEY"] = file_1_display_key
            reconciliation_result["MATCHED COMPOSITE KEY"] = file_2_display_key or ""
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
        for rule_index, (
            (file_1_fields, file_2_fields),
            comparison_type,
            (combination_type_file_1, combination_type_file_2),
            decimal_precision,
            number_format_file_1,
            number_format_file_2,
        ) in enumerate(zip(
            normalized_rules,
            comparison_types,
            prepared.combination_types,
            comparison_precisions,
            number_formats_1,
            number_formats_2,
        )):
            batch = column_text_batches.get(rule_index)
            numeric_batch = column_numeric_batches.get(rule_index)
            identifier_batch = column_identifier_batches.get(rule_index)
            date_batch = column_date_batches.get(rule_index)
            pair_position = int(column_pair_positions[outcome_index])
            if batch is not None and pair_position >= 0 and batch.equal[pair_position]:
                field_rules.extend(batch.normalization_rules.get(pair_position, ()))
                differences = {}
            elif batch is not None and pair_position >= 0 and batch.scores[pair_position] >= 0:
                label = fields_label(file_1_fields)
                score = int(batch.scores[pair_position])
                status = (
                    "Minor spelling variation" if score >= 85
                    else "Possible Match - Manual Review" if score >= 75
                    else "Mismatch"
                )
                differences = {
                    f"{label} (FILE 1)": batch.values_1[pair_position],
                    f"{label} (FILE 2)": batch.values_2[pair_position],
                    f"{label} CONFIDENCE": f"{score}%",
                    f"{label} STATUS": status,
                }
            elif (
                numeric_batch is not None
                and pair_position >= 0
                and numeric_batch.valid[pair_position]
            ):
                if numeric_batch.equal[pair_position]:
                    differences = {}
                else:
                    label = fields_label(file_1_fields)
                    differences = {
                        f"{label} (FILE 1)": file_1_row.get(file_1_fields[0]),
                        f"{label} (FILE 2)": file_2_row.get(file_2_fields[0]),
                        f"{label} DIFF": float(numeric_batch.differences[pair_position]),
                        f"{label} STATUS": "Mismatch",
                    }
            elif (
                identifier_batch is not None
                and pair_position >= 0
                and identifier_batch.valid[pair_position]
            ):
                confidence = int(identifier_batch.confidence[pair_position])
                if confidence == 100:
                    differences = {}
                else:
                    label = fields_label(file_1_fields)
                    differences = {
                        f"{label} (FILE 1)": collect_rule_value(
                                file_1_row, file_1_fields, known_matcher_type=comparison_type
                        )[0],
                        f"{label} (FILE 2)": collect_rule_value(
                                file_2_row, file_2_fields, known_matcher_type=comparison_type
                        )[0],
                        f"{label} CONFIDENCE": f"{confidence}%",
                        f"{label} STATUS": (
                            "Identifier formatting difference only" if confidence == 97 else "Mismatch"
                        ),
                    }
            elif date_batch is not None and pair_position >= 0 and date_batch.valid[pair_position]:
                if date_batch.equal[pair_position]:
                    differences = {}
                else:
                    label = fields_label(file_1_fields)
                    differences = {
                        f"{label} (FILE 1)": collect_rule_value(
                            file_1_row, file_1_fields, known_matcher_type=comparison_type
                        )[0],
                        f"{label} (FILE 2)": collect_rule_value(
                            file_2_row, file_2_fields, known_matcher_type=comparison_type
                        )[0],
                        f"{label} CONFIDENCE": "0%",
                        f"{label} STATUS": "Mismatch",
                    }
            else:
                differences = compare_rule_values(
                    file_1_row,
                    file_2_row,
                    file_1_fields,
                    file_2_fields,
                    field_rules,
                    matcher_type_override=comparison_type,
                    decimal_precision=decimal_precision,
                    number_format_file_1=number_format_file_1,
                    number_format_file_2=number_format_file_2,
                    combination_type_file_1=combination_type_file_1,
                    combination_type_file_2=combination_type_file_2,
                )
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
        if compact_records:
            # At this point every required output value has been copied into a
            # result category. Drop source row dictionaries as we go so the
            # temporary matching representation does not overlap the result.
            outcome["row"] = outcome["file_2_row"] = outcome["candidate_row"] = None

    # Tolerance bands: accept small differences now that matching is done,
    # before anything is counted or written.
    tolerance = apply_tolerance_bands(
        reconciliation_results, matched_records, tolerance_bands, compared_labels,
        stays_in_review=lambda record: record["IDENTITY CLASSIFICATION"] == MatchClassification.EXCEPTION_MATCH.value,
    )
    field_discrepancy_count -= tolerance.cleared_records
    reconciliation_record_count = len(reconciliation_results)
    confidence_review_count = sum(
        1 for row in reconciliation_results
        if int(str(row.get("MATCH CONFIDENCE", "100")).replace("%", "") or 0) < 100
    )

    tracker.comparing_columns()

    if is_cancelled and is_cancelled():
        raise InterruptedError("Reconciliation cancelled by user")

    file_2_indices_set = set(file_2_df.index)
    unmatched_indices = file_2_indices_set - matched_file_2_indices
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
                    "Record": display_key(
                        file_2_records[i], prepared.display_key_columns_2, prepared.display_keys_nonblank_2
                    ),
                    **clean_f2_row, **referenced_values(rule, file_2_records[i]),
                })
                continue
            if note := grouped_rows_note(file_2_records[i]):
                clean_f2_row["GROUPED ROWS"] = note
            file_2_not_found.append(clean_f2_row)
    if compact_records:
        file_2_records.clear()

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
        comparison_field_labels=[fields_label(left) for left, _ in normalized_rules],
        consume_reconciliation_results=request.engine_mode == "columns",
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
            "numeric_precision": precision_metadata,
            "number_formats": number_format_metadata,
            "engine": request.engine_mode,
            "engine_version": _ENGINE_VERSIONS[request.engine_mode],
            "resolution_rules": [f"{rule.label()}: {describe_rule(rule)}" for rule in (evaluator.rules if evaluator else [])],
            "resolution_rules_used": dict(evaluator.counts) if evaluator else {},
            "tolerance_bands": [band.describe() for band in tolerance_bands],
            "tolerance_counts": {"fields": tolerance.accepted_fields, "records": tolerance.moved_to_matched},
            EXPLANATION_METADATA_KEY: explanation_metadata(
                secondary_names=secondary_names if normalized_secondary_conditions else None,
            ),
        },
    )
    universal_data["auto_resolved"] = auto_resolved
    if request.engine_mode == "columns":
        reconciliation_results.clear()

    if write_report:
        generate_enterprise_report(universal_data, {}, output_path)

    tracker.finalized()

    identity_counts = universal_data["statistics"]["identity"]
    summary_stats = {
        "report_rows": reconciliation_record_count,
        "only_in_file_1": len(file_1_not_found),
        "only_in_file_2": len(file_2_not_found),
        "confidence_review": confidence_review_count,
        "source_records": len(file_1_df),
        "destination_records": len(file_2_df),
        "matched_records": len(matched_file_2_indices),
        "fully_matched_records": len(matched_file_2_indices) - reconciliation_record_count,
        "field_discrepancies": field_discrepancy_count,
        "exact_matches": identity_counts[MatchClassification.EXACT_MATCH.value],
        "exception_matches": identity_counts[MatchClassification.EXCEPTION_MATCH.value],
        "ambiguous_matches": identity_counts[MatchClassification.AMBIGUOUS_MATCH.value],
        "not_found_matches": identity_counts[MatchClassification.NOT_FOUND.value],
        "keyless_matches": sum(count for label, count in pass_counts.items() if label != "Primary key"),
        "normalized_matches": count_populated_field(identity_resolution, "NORMALIZATION APPLIED"),
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

    gstr_groups_2 = {gstr: group for gstr, group in df2.groupby("__KEY_GSTR")}
    gstr_matchers_2 = {
        gstr: IndexedCandidateMatcher(group, "INVOICE NO.", "invoice")
        for gstr, group in gstr_groups_2.items()
    }

    mismatched: list[dict] = []
    only_in_file1: list[dict] = []
    confidence_review: list[dict] = []
    matched_records: list[dict] = []
    # Pairs to confirm, in the form the Match Review list and tab read.
    identity_resolution: list[dict] = []
    matched_file2_indices: set = set()

    tracker.matching_records()

    df1_records = df1.to_dict('records')
    for row_idx, row1 in enumerate(df1_records):
        if is_cancelled and row_idx % 25 == 0 and is_cancelled():
            raise InterruptedError("Reconciliation cancelled by user")

        gstr_key = row1.get("__KEY_GSTR")

        matcher = gstr_matchers_2.get(gstr_key)
        if matcher is None:
            clean_r1 = {k: v for k, v in row1.items() if k != "_ROW_NO" and not str(k).startswith("__")}
            clean_r1["ROW (File 1)"] = row1.get("_ROW_NO", row_idx + 2)
            only_in_file1.append(clean_r1)
            continue

        exact_invoice_candidates = matcher.find_exact_candidates(
            row1.get("INVOICE NO."), matched_file2_indices
        )
        if len(exact_invoice_candidates) == 1:
            best_idx, row2, invoice_result = exact_invoice_candidates[0]
        else:
            best_idx, row2, invoice_result = matcher.find_best_match(
                row1.get("INVOICE NO."),
                "INVOICE NO.",
                matched_file2_indices,
            )

        if row2 is None or invoice_result is None:
            clean_r1 = {k: v for k, v in row1.items() if k != "_ROW_NO" and not str(k).startswith("__")}
            clean_r1["ROW (File 1)"] = row1.get("_ROW_NO", row_idx + 2)
            only_in_file1.append(clean_r1)
            continue

        matched_file2_indices.add(best_idx)
        invoice_1, invoice_2 = row1.get("INVOICE NO."), row2.get("INVOICE NO.")
        base = {
            "ROW (File 1)": row1.get("_ROW_NO", row_idx + 2),
            "ROW (File 2)": row2.get("_ROW_NO", best_idx + 2),
            # Identify the invoice on every difference line of the report.
            "MATCH KEY": f"{row1.get('GSTR')} | {invoice_1}",
            "MATCHED KEY": f"{row2.get('GSTR')} | {invoice_2}",
            "GSTR": row1.get("GSTR"),
            "INVOICE NO. (File1)": row1.get("INVOICE NO."),
            "INVOICE NO. (File2)": row2.get("INVOICE NO."),
            "INVOICE MATCH CONFIDENCE": f"{invoice_result.confidence}%",
            "INVOICE MATCH STATUS": invoice_result.status,
            "NAME (File1)": row1.get("NAME OF TRADER/FIRM/COMPANY", ""),
            "NAME (File2)": row2.get("NAME OF TRADER/FIRM/COMPANY", ""),
        }

        # Differences use the shared report's names ("FIELD (FILE 1)", "FIELD
        # (FILE 2)", "FIELD STATUS", "FIELD DIFF"), so each one is listed on the
        # Differences tab and in the stored results, not only counted.
        field_diffs: dict[str, object] = {}
        review_notes: dict[str, object] = {}
        name_1 = row1.get("NAME OF TRADER/FIRM/COMPANY", "")
        name_2 = row2.get("NAME OF TRADER/FIRM/COMPANY", "")

        name_result = compare_values(name_1, name_2, "NAME OF TRADER/FIRM/COMPANY", "NAME OF TRADER/FIRM/COMPANY", "company_name")
        if not name_result.matched or name_result.confidence < text_threshold:
            field_diffs["NAME OF TRADER/FIRM/COMPANY (FILE 1)"] = name_1
            field_diffs["NAME OF TRADER/FIRM/COMPANY (FILE 2)"] = name_2
            field_diffs["NAME OF TRADER/FIRM/COMPANY STATUS"] = name_result.status
            field_diffs["NAME OF TRADER/FIRM/COMPANY CONFIDENCE"] = f"{name_result.confidence}%"
        elif name_result.confidence < 100:
            review_notes["NAME STATUS"] = name_result.status
            review_notes["NAME CONFIDENCE"] = f"{name_result.confidence}%"

        date_result = compare_values(row1.get("INVOICE DATE"), row2.get("INVOICE DATE"), "INVOICE DATE", "INVOICE DATE", "date")
        if not date_result.matched:
            field_diffs["INVOICE DATE (FILE 1)"] = row1.get("INVOICE DATE")
            field_diffs["INVOICE DATE (FILE 2)"] = row2.get("INVOICE DATE")
            field_diffs["INVOICE DATE STATUS"] = date_result.status
            field_diffs["INVOICE DATE DETAIL"] = date_result.detail

        for col in GST_AMOUNT_COLUMNS:
            amount_result = compare_values(row1.get(col, 0), row2.get(col, 0), col, col, "numeric")
            if not amount_result.matched:
                value1 = float(row1.get(col, 0) or 0)
                value2 = float(row2.get(col, 0) or 0)
                field_diffs[f"{col} (FILE 1)"] = value1
                field_diffs[f"{col} (FILE 2)"] = value2
                field_diffs[f"{col} DIFF"] = round(value1 - value2, 2)
                field_diffs[f"{col} STATUS"] = amount_result.status

        if field_diffs:
            record = dict(base)
            record.update(field_diffs)
            mismatched.append(record)
        elif invoice_result.confidence < 100 or review_notes:
            record = dict(base)
            record.update(review_notes)
            confidence_review.append(record)
            reasons = []
            if invoice_result.confidence < 100:
                reasons.append(f"Invoice number {invoice_1} was paired with {invoice_2} ({invoice_result.confidence}% similar).")
            if review_notes:
                reasons.append(f"Supplier name '{name_1}' is {name_result.confidence}% similar to '{name_2}'.")
            identity_resolution.append({
                "ROW (FILE 1)": base["ROW (File 1)"],
                "IDENTITY CLASSIFICATION": MatchClassification.EXCEPTION_MATCH.value,
                "MATCH EXPLANATION": " ".join(reasons) + " Confirm that this is the same invoice.",
                "MATCH TYPE": "invoice",
                "MATCH CONFIDENCE": f"{min(invoice_result.confidence, name_result.confidence)}%",
                "MATCH KEY": base["MATCH KEY"],
                "CANDIDATE KEY": base["MATCHED KEY"],
            })
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
            clean_r2 = {k: v for k, v in df2_records[i].items() if k != "_ROW_NO" and not str(k).startswith("__")}
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
        identity_resolution=identity_resolution,
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
