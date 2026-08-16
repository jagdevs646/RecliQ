from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from app.reconciliation_engine.engine import (
    GST_AMOUNT_COLUMNS as AMOUNT_COLUMNS,
    GST_MERGE_KEY_COLUMNS as MERGE_KEY_COLUMNS,
    GST_TEXT_REVIEW_THRESHOLD as TEXT_REVIEW_THRESHOLD,
    REQUIRED_GST_COLUMNS as REQUIRED_COLUMNS,
    run_gst_reconciliation as run_gst_reconciliation_impl,
    validate_gst_columns as validate_columns_impl,
)
from app.reconciliation_engine.preprocessing import (
    merge_duplicate_invoices as merge_duplicate_invoices_impl,
    normalise_gst_df as normalise_df_impl,
)
from app.reconciliation_engine.report_generator import (
    auto_fit_columns as auto_fit_columns_impl,
    excel_writer_engine as excel_writer_engine_impl,
    generate_sample_format as generate_sample_format_impl,
    style_header_row as style_header_row_impl,
    write_gst_output as write_output_impl,
)


@dataclass
class GSTReconciliationOutcome:
    mismatched: list[dict]
    only_in_file_1: list[dict]
    only_in_file_2: list[dict]
    confidence_review: list[dict]
    matched_records: int


def normalise_df(df: pd.DataFrame) -> pd.DataFrame:
    return normalise_df_impl(df, AMOUNT_COLUMNS)


def validate_columns(df: pd.DataFrame) -> list[str]:
    return validate_columns_impl(df)


def merge_duplicate_invoices(df: pd.DataFrame) -> pd.DataFrame:
    return merge_duplicate_invoices_impl(df, MERGE_KEY_COLUMNS, AMOUNT_COLUMNS)


def excel_writer_engine() -> str:
    return excel_writer_engine_impl()


def auto_fit_columns(worksheet, df: pd.DataFrame) -> None:
    auto_fit_columns_impl(worksheet, df)


def style_header_row(workbook, worksheet, df: pd.DataFrame, color: str) -> None:
    style_header_row_impl(workbook, worksheet, df, color)


def generate_sample_format(path: Path) -> None:
    generate_sample_format_impl(path, REQUIRED_COLUMNS)


def _sheet_name(label: str) -> str:
    return label[:31]


def _format_date_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy(deep=False)
    if "INVOICE DATE" in df.columns:
        df["INVOICE DATE"] = df["INVOICE DATE"].apply(
            lambda value: value.date().isoformat() if pd.notna(value) and hasattr(value, "date") else value
        )
    return df


def reconcile(
    df1: pd.DataFrame,
    df2: pd.DataFrame,
    text_threshold: int = TEXT_REVIEW_THRESHOLD,
) -> GSTReconciliationOutcome:
    from app.reconciliation_engine.matching import IndexedCandidateMatcher, compare_values

    mismatched: list[dict] = []
    only_in_file1: list[dict] = []
    confidence_review: list[dict] = []
    matched_file2_indices: set = set()

    gstr_groups_2 = {gstr: group for gstr, group in df2.groupby("GSTR")}
    gstr_matchers_2 = {
        gstr: IndexedCandidateMatcher(group, "INVOICE NO.", "invoice")
        for gstr, group in gstr_groups_2.items()
    }

    df1_records = df1.to_dict('records')
    for row_idx, row1 in enumerate(df1_records):
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

        for col in AMOUNT_COLUMNS:
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

    df2_records = df2.to_dict('records')
    only_in_file2 = []
    for i, idx in enumerate(df2.index):
        if idx not in matched_file2_indices:
            clean_r2 = {k: v for k, v in df2_records[i].items() if k != "_ROW_NO"}
            clean_r2["ROW (File 2)"] = df2_records[i].get("_ROW_NO", idx + 2)
            only_in_file2.append(clean_r2)

    return GSTReconciliationOutcome(
        mismatched=mismatched,
        only_in_file_1=only_in_file1,
        only_in_file_2=only_in_file2,
        confidence_review=confidence_review,
        matched_records=len(matched_file2_indices),
    )


def write_output(
    mismatched: list,
    only_in_file1: list,
    only_in_file2: list,
    confidence_review: list,
    path: Path,
    file1_name: str = "File1",
    file2_name: str = "File2",
) -> None:
    write_output_impl(
        mismatched,
        only_in_file1,
        only_in_file2,
        confidence_review,
        path,
        file1_name=file1_name,
        file2_name=file2_name,
    )


def run_gst_reconciliation(
    file_1_path: Path,
    file_2_path: Path,
    output_path: Path,
    orientation: str = "vertical",
    text_threshold: int = TEXT_REVIEW_THRESHOLD,
) -> dict:
    return run_gst_reconciliation_impl(
        file_1_path,
        file_2_path,
        output_path,
        orientation=orientation,
        text_threshold=text_threshold,
    )
