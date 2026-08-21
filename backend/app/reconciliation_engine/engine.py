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
    compare_values,
    detect_matcher_type,
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


def run_generic_reconciliation(
    file_1_path: Path,
    file_2_path: Path,
    output_path: Path,
    key_file_1: str,
    key_file_2: str,
    rules: list[dict],
    orientation: str = "vertical",
    include_columns_file_1: list[str] | None = None,
    include_columns_file_2: list[str] | None = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    file_1_name: str = "File 1",
    file_2_name: str = "File 2",
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> dict:
    tracker = ProgressTracker(progress_callback)
    tracker.reading_excel()

    raw_f1 = pd.read_excel(file_1_path)
    raw_f2 = pd.read_excel(file_2_path)
    raw_f1["_ROW_NO"] = raw_f1.index + 2
    raw_f2["_ROW_NO"] = raw_f2.index + 2

    file_1_df = prepare_dataframe(raw_f1, orientation=orientation)
    file_2_df = prepare_dataframe(raw_f2, orientation=orientation)

    file_1_id_col = normalize_header(key_file_1)
    file_2_id_col = normalize_header(key_file_2)
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

    if not normalized_rules:
        raise ValueError("At least one reconciliation rule is required.")

    validate_columns(file_1_df, [file_1_id_col, *file_1_extra], "File 1")
    validate_columns(file_2_df, [file_2_id_col, *file_2_extra], "File 2")
    for left, right in normalized_rules:
        validate_columns(file_1_df, left, "File 1 rule")
        validate_columns(file_2_df, right, "File 2 rule")
    validate_combined_numeric_rules(file_1_df, file_2_df, normalized_rules)

    file_1_df = aggregate_by_key(file_1_df, file_1_id_col)
    file_2_df = aggregate_by_key(file_2_df, file_2_id_col)

    if is_cancelled and is_cancelled():
        raise InterruptedError("Reconciliation cancelled by user")

    tracker.building_indexes()

    key_type = detect_matcher_type(
        list(file_1_df[file_1_id_col]) + list(file_2_df[file_2_id_col]),
        file_1_id_col,
        file_2_id_col,
    )

    indexed_matcher = IndexedCandidateMatcher(file_2_df, file_2_id_col, key_type)

    reconciliation_results: list[dict] = []
    file_1_not_found: list[dict] = []
    matched_records: list[dict] = []
    matched_file_2_indices: set = set()

    tracker.matching_records()

    file_1_records = file_1_df.to_dict('records')
    for row_idx, file_1_row in enumerate(file_1_records):
        if is_cancelled and row_idx % 25 == 0 and is_cancelled():
            raise InterruptedError("Reconciliation cancelled by user")

        file_1_id = file_1_row.get(file_1_id_col)

        best_idx, file_2_row, key_result = indexed_matcher.find_best_match(
            file_1_id,
            file_1_id_col,
            matched_file_2_indices,
        )

        if file_2_row is None or key_result is None:
            clean_f1_row = {k: v for k, v in file_1_row.items() if k != "_ROW_NO"}
            clean_f1_row["ROW (FILE 1)"] = file_1_row.get("_ROW_NO", row_idx + 2)
            file_1_not_found.append(clean_f1_row)
            continue

        matched_file_2_indices.add(best_idx)
        reconciliation_result = {
            "ROW (FILE 1)": file_1_row.get("_ROW_NO", row_idx + 2),
            "ROW (FILE 2)": file_2_row.get("_ROW_NO", best_idx + 2),
            file_1_id_col: file_1_id,
            f"MATCHED {file_2_id_col}": file_2_row.get(file_2_id_col),
            "MATCH TYPE": key_result.matcher_type,
            "MATCH CONFIDENCE": f"{key_result.confidence}%",
            "MATCH STATUS": key_result.status,
        }

        for col in file_1_extra:
            if col != "_ROW_NO":
                reconciliation_result[col] = file_1_row.get(col)
        for col in file_2_extra:
            if col != "_ROW_NO":
                reconciliation_result[f"{col} (FILE 2)"] = file_2_row.get(col)

        has_reportable_issue = key_result.confidence < 100
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
            clean_f2_row = {k: v for k, v in file_2_records[i].items() if k != "_ROW_NO"}
            clean_f2_row["ROW (FILE 2)"] = file_2_records[i].get("_ROW_NO", idx + 2)
            file_2_not_found.append(clean_f2_row)

    tracker.generating_report()
    
    universal_data = build_universal_data_model(
        job_type="generic",
        file_1_name=file_1_name,
        file_2_name=file_2_name,
        matching_keys=[key_file_1],
        reconciliation_results=reconciliation_results,
        file_1_not_found=file_1_not_found,
        file_2_not_found=file_2_not_found,
        matched_records=matched_records,
        total_file_1=len(file_1_df),
        total_file_2=len(file_2_df),
    )
    
    # Store the raw universal data as well, so it can be retrieved for custom report generation.
    from app.utils.json_encoder import safe_json_dump
    raw_path = output_path.with_name(f"{output_path.stem}_data.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        safe_json_dump(universal_data, f)
        
    generate_enterprise_report(universal_data, {}, output_path)

    tracker.finalized()

    return {
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
    }


def run_gst_reconciliation(
    file_1_path: Path,
    file_2_path: Path,
    output_path: Path,
    orientation: str = "vertical",
    text_threshold: int = GST_TEXT_REVIEW_THRESHOLD,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    file_1_name: str = "File1",
    file_2_name: str = "File2",
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> dict:
    tracker = ProgressTracker(progress_callback)
    tracker.reading_excel()

    raw1 = pd.read_excel(file_1_path)
    raw2 = pd.read_excel(file_2_path)
    raw1["_ROW_NO"] = raw1.index + 2
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
    
    from app.utils.json_encoder import safe_json_dump
    raw_path = output_path.with_name(f"{output_path.stem}_data.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        safe_json_dump(universal_data, f)
        
    generate_enterprise_report(universal_data, {}, output_path)

    tracker.finalized()

    return {
        "report_rows": len(mismatched),
        "only_in_file_1": len(only_in_file1),
        "only_in_file_2": len(only_in_file2),
        "confidence_review": len(confidence_review),
        "source_records": len(df1),
        "destination_records": len(df2),
        "matched_records": len(matched_file2_indices),
        "fully_matched_records": len(matched_file2_indices) - len(mismatched) - len(confidence_review),
    }
