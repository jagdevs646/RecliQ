from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from app.reconciliation_engine.cache import (
    is_blank,
    normalize_header,
    to_number,
)
from app.reconciliation_engine.matching import (
    IndexedCandidateMatcher,
    MatchClassification,
    MatchResult,
    compare_values,
    detect_matcher_type,
    match_threshold,
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
from app.reconciliation_engine.normalization import normalize_dataframe
from app.reconciliation_engine.matching.advanced_matcher import find_duplicates, group_for_many_to_one
from app.reconciliation_engine.progress_tracker import ProgressTracker
from app.reconciliation_engine.report_generator import (
    write_generic_report,
    write_gst_output,
)
from app.reconciliation_engine.utilities import (
    collect_rule_value,
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


def compare_rule_values(
    file_1_row: dict,
    file_2_row: dict,
    file_1_fields: list[str],
    file_2_fields: list[str],
) -> dict:
    file_1_value, type_hint_1 = collect_rule_value(file_1_row, file_1_fields)
    file_2_value, type_hint_2 = collect_rule_value(file_2_row, file_2_fields)
    label = fields_label(file_1_fields)
    matcher_type = type_hint_1 or type_hint_2

    if matcher_type is None:
        matcher_type = detect_matcher_type(
            [file_1_value, file_2_value],
            fields_label(file_1_fields),
            fields_label(file_2_fields),
        )

    result = compare_values(
        file_1_value,
        file_2_value,
        fields_label(file_1_fields),
        fields_label(file_2_fields),
        matcher_type,
    )

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


def _identity_explanation(
    classification: MatchClassification,
    result: MatchResult | None = None,
    threshold: int | None = None,
    secondary_details: list[str] | None = None,
) -> str:
    conditions = ", ".join(secondary_details or [])
    if classification is MatchClassification.EXACT_MATCH:
        return "Primary key matched exactly after deterministic normalization."
    if classification is MatchClassification.AMBIGUOUS_MATCH:
        return (
            "More than one unused destination record passed every secondary condition"
            f" ({conditions or 'configured conditions'}); no record was selected."
        )
    if classification is MatchClassification.NOT_FOUND and result is None:
        return (
            "No unused destination record passed every secondary condition"
            f" ({conditions or 'no deterministic candidate'})."
        )
    assert result is not None
    return (
        f"Primary key comparison used {result.matcher_type}: {result.status} "
        f"({result.confidence}% vs required {threshold}%). "
        f"{result.detail or 'The candidate was narrowed by configured secondary conditions.'}"
    ).strip()


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
) -> dict:
    tracker = ProgressTracker(progress_callback)
    tracker.reading_excel()

    # Preprocess incoming DFs to ensure row numbers and basic normalization
    if "_ROW_NO" not in file_1_df.columns:
        file_1_df["_ROW_NO"] = file_1_df.index + 2
    if "_ROW_NO" not in file_2_df.columns:
        file_2_df["_ROW_NO"] = file_2_df.index + 2
        
    file_1_id_col = [normalize_header(k) for k in key_file_1]
    file_2_id_col = [normalize_header(k) for k in key_file_2]
    if len(file_1_id_col) != len(file_2_id_col):
        raise ValueError("File 1 and File 2 must use the same number of primary-key columns.")
    if not file_1_id_col:
        raise ValueError("At least one primary-key column is required.")

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

    validate_columns(file_1_df, [*file_1_id_col, *file_1_extra], "File 1")
    validate_columns(file_2_df, [*file_2_id_col, *file_2_extra], "File 2")
    for condition in normalized_secondary_conditions:
        validate_columns(file_1_df, [condition["source_column"]], "File 1 secondary condition")
        validate_columns(file_2_df, [condition["destination_column"]], "File 2 secondary condition")
    for left, right in normalized_rules:
        validate_columns(file_1_df, left, "File 1 rule")
        validate_columns(file_2_df, right, "File 2 rule")
    validate_combined_numeric_rules(file_1_df, file_2_df, normalized_rules)
    
    # 1. Normalize data
    date_cols = list(dict.fromkeys(
        [rule[0][0] for rule in normalized_rules if 'date' in rule[0][0].lower()]
        + [key for key in file_1_id_col if 'date' in key.lower()]
    ))
    text_cols = [rule[0][0] for rule in normalized_rules if 'name' in rule[0][0].lower() or 'vendor' in rule[0][0].lower()]
    num_cols = [rule[0][0] for rule in normalized_rules if 'amount' in rule[0][0].lower() or 'value' in rule[0][0].lower() or 'tax' in rule[0][0].lower()]
    
    f1_norm_config = {"date_columns": date_cols, "text_columns": text_cols, "number_columns": num_cols, "id_columns": file_1_id_col}
    f2_date_cols = list(dict.fromkeys(
        [r[1][0] for r in normalized_rules if r[0][0] in date_cols]
        + [key for key in file_2_id_col if 'date' in key.lower()]
    ))
    f2_norm_config = {"date_columns": f2_date_cols,
                      "text_columns": [r[1][0] for r in normalized_rules if r[0][0] in text_cols], 
                      "number_columns": [r[1][0] for r in normalized_rules if r[0][0] in num_cols],
                      "id_columns": file_2_id_col}
                      
    file_1_df = normalize_dataframe(file_1_df, f1_norm_config)
    file_2_df = normalize_dataframe(file_2_df, f2_norm_config)

    # Use normalized keys for matching
    file_1_match_keys = [f"NORM_{k}" if f"NORM_{k}" in file_1_df.columns else k for k in file_1_id_col]
    file_2_match_keys = [f"NORM_{k}" if f"NORM_{k}" in file_2_df.columns else k for k in file_2_id_col]

    # Handle duplicates & Grouping (One-to-many / Many-to-one)
    file_1_df, f1_dupes = find_duplicates(file_1_df, file_1_match_keys)
    file_2_df, f2_dupes = find_duplicates(file_2_df, file_2_match_keys)
    
    file_1_df = group_for_many_to_one(file_1_df, file_1_match_keys, num_cols)
    file_2_df = group_for_many_to_one(file_2_df, file_2_match_keys, f2_norm_config["number_columns"])
    
    if is_cancelled and is_cancelled():
        raise InterruptedError("Reconciliation cancelled by user")

    tracker.building_indexes()

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
    if len(key_matcher_types) == 1 and key_matcher_types[0] == "date" and not date_only_override:
        raise ValueError(
            "Date-only matching can be ambiguous because multiple transactions may occur on the same date. "
            "Select at least one additional identifying column or explicitly enable the date-only override."
        )
    indexed_matcher = IndexedCandidateMatcher(file_2_df, file_2_match_keys, key_matcher_types)
    is_composite_key = len(file_1_match_keys) > 1
    primary_key_1 = file_1_match_keys[0]
    primary_key_2 = file_2_match_keys[0]

    reconciliation_results: list[dict] = []
    file_1_not_found: list[dict] = []
    matched_records: list[dict] = []
    matched_file_2_indices: set = set()
    identity_resolution: list[dict] = []

    tracker.matching_records()

    allowed_f1_cols = set(file_1_match_keys + file_1_id_col + [r[0][i] for r in normalized_rules for i in range(len(r[0]))] + file_1_extra)
    allowed_f2_cols = set(file_2_match_keys + file_2_id_col + [r[1][i] for r in normalized_rules for i in range(len(r[1]))] + file_2_extra)

    file_1_records = file_1_df.to_dict('records')
    for row_idx, file_1_row in enumerate(file_1_records):
        if is_cancelled and row_idx % 25 == 0 and is_cancelled():
            raise InterruptedError("Reconciliation cancelled by user")

        file_1_id = (
            [file_1_row.get(key) for key in file_1_match_keys]
            if is_composite_key
            else file_1_row.get(primary_key_1)
        )

        exact_candidates = indexed_matcher.find_exact_candidates(file_1_id, matched_file_2_indices)
        classification = MatchClassification.NOT_FOUND
        secondary_details: list[str] = []
        key_result: MatchResult | None = None
        required_threshold: int | None = None
        best_idx = None
        file_2_row = None

        if len(exact_candidates) == 1:
            best_idx, file_2_row, key_result = exact_candidates[0]
            classification = MatchClassification.EXACT_MATCH
        elif len(exact_candidates) > 1:
            classification = MatchClassification.AMBIGUOUS_MATCH
        elif normalized_secondary_conditions:
            secondary_candidates, secondary_details = indexed_matcher.find_secondary_candidates(
                file_1_row,
                normalized_secondary_conditions,
                matched_file_2_indices,
            )
            if len(secondary_candidates) > 1:
                classification = MatchClassification.AMBIGUOUS_MATCH
            elif len(secondary_candidates) == 1:
                candidate_idx, candidate_row = secondary_candidates[0]
                similarity_result, required_threshold = _primary_similarity_result(
                    file_1_row,
                    candidate_row,
                    file_1_id_col,
                    file_2_id_col,
                    key_matcher_types,
                    similarity_policy,
                )
                key_result = similarity_result
                if similarity_result.confidence >= required_threshold:
                    best_idx, file_2_row = candidate_idx, candidate_row
                    classification = MatchClassification.EXCEPTION_MATCH

        explanation = _identity_explanation(
            classification,
            key_result,
            required_threshold,
            secondary_details,
        )
        identity_resolution.append(
            {
                "ROW (FILE 1)": file_1_row.get("_ROW_NO", row_idx + 2),
                "IDENTITY CLASSIFICATION": classification.value,
                "MATCH EXPLANATION": explanation,
                "MATCH TYPE": key_result.matcher_type if key_result else "",
                "MATCH CONFIDENCE": f"{key_result.confidence}%" if key_result else "0%",
                "MATCH THRESHOLD": f"{required_threshold}%" if required_threshold is not None else "",
            }
        )

        if file_2_row is None or key_result is None:
            clean_f1_row = {k: v for k, v in file_1_row.items() if k in allowed_f1_cols}
            clean_f1_row["ROW (FILE 1)"] = file_1_row.get("_ROW_NO", row_idx + 2)
            clean_f1_row["IDENTITY CLASSIFICATION"] = classification.value
            clean_f1_row["MATCH EXPLANATION"] = explanation
            file_1_not_found.append(clean_f1_row)
            continue

        matched_file_2_indices.add(best_idx)
        
        # Check if this was a many-to-one or one-to-many match
        group_status = "One-to-One Match"
        if file_1_row.get("__GROUP_COUNT__", 1) > 1 and file_2_row.get("__GROUP_COUNT__", 1) == 1:
            group_status = "Many-to-One Match"
        elif file_1_row.get("__GROUP_COUNT__", 1) == 1 and file_2_row.get("__GROUP_COUNT__", 1) > 1:
            group_status = "One-to-Many Match"
            
        reconciliation_result = {
            "ROW (FILE 1)": file_1_row.get("_ROW_NO", row_idx + 2),
            "ROW (FILE 2)": file_2_row.get("_ROW_NO", best_idx + 2),
            "MATCH TYPE": key_result.matcher_type,
            "MATCH CONFIDENCE": f"{key_result.confidence}%",
            "MATCH STATUS": key_result.status,
            "IDENTITY CLASSIFICATION": classification.value,
            "MATCH EXPLANATION": explanation,
            "GROUP CLASSIFICATION": group_status
        }
        if is_composite_key:
            reconciliation_result["COMPOSITE MATCH KEY"] = " | ".join(
                str(file_1_row.get(column, "")) for column in file_1_id_col
            )
            reconciliation_result["MATCHED COMPOSITE KEY"] = " | ".join(
                str(file_2_row.get(column, "")) for column in file_2_id_col
            )
            for source_column, destination_column in zip(file_1_id_col, file_2_id_col):
                reconciliation_result[source_column] = file_1_row.get(source_column)
                reconciliation_result[f"MATCHED {destination_column}"] = file_2_row.get(destination_column)
        else:
            reconciliation_result[primary_key_1] = file_1_id
            reconciliation_result[f"MATCHED {primary_key_2}"] = file_2_row.get(primary_key_2)

        for col in file_1_extra:
            if col != "_ROW_NO":
                reconciliation_result[col] = file_1_row.get(col)
        for col in file_2_extra:
            if col != "_ROW_NO":
                reconciliation_result[f"{col} (FILE 2)"] = file_2_row.get(col)

        has_reportable_issue = classification is MatchClassification.EXCEPTION_MATCH
        for file_1_fields, file_2_fields in normalized_rules:
            differences = compare_rule_values(file_1_row, file_2_row, file_1_fields, file_2_fields)
            if differences:
                has_reportable_issue = True
                reconciliation_result.update(differences)

        if has_reportable_issue:
            reconciliation_results.append(reconciliation_result)
        else:
            matched_records.append(reconciliation_result)

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
            clean_f2_row["ROW (FILE 2)"] = file_2_records[i].get("_ROW_NO", idx + 2)
            file_2_not_found.append(clean_f2_row)
            
    # Add duplicates to results as separate classification
    for _, row in f1_dupes.iterrows():
        clean_row = {k: v for k, v in row.items() if k in allowed_f1_cols}
        clean_row["CLASSIFICATION"] = "Duplicate"
        file_1_not_found.append(clean_row)
        
    for _, row in f2_dupes.iterrows():
        clean_row = {k: v for k, v in row.items() if k in allowed_f2_cols}
        clean_row["CLASSIFICATION"] = "Duplicate"
        file_2_not_found.append(clean_row)

    tracker.generating_report()
    
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
    )

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
        "exact_matches": sum(item["IDENTITY CLASSIFICATION"] == MatchClassification.EXACT_MATCH.value for item in identity_resolution),
        "exception_matches": sum(item["IDENTITY CLASSIFICATION"] == MatchClassification.EXCEPTION_MATCH.value for item in identity_resolution),
        "ambiguous_matches": sum(item["IDENTITY CLASSIFICATION"] == MatchClassification.AMBIGUOUS_MATCH.value for item in identity_resolution),
        "not_found_matches": sum(item["IDENTITY CLASSIFICATION"] == MatchClassification.NOT_FOUND.value for item in identity_resolution),
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
