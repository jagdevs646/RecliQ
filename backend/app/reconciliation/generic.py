from __future__ import annotations

from pathlib import Path

from app.reconciliation_engine.engine import (
    compare_rule_values as compare_rule_values_impl,
    run_generic_reconciliation as run_generic_reconciliation_impl,
)
from app.reconciliation_engine.preprocessing import (
    aggregate_by_key as aggregate_by_key_impl,
    fields_label as fields_label_impl,
    normalize_fields as normalize_fields_impl,
    read_excel_columns as read_excel_columns_impl,
)
from app.reconciliation_engine.report_generator import (
    auto_fit_columns as auto_fit_columns_impl,
    excel_writer_engine as excel_writer_engine_impl,
    write_generic_report as write_generic_report_impl,
)
from app.reconciliation_engine.utilities import (
    collect_rule_value as collect_rule_value_impl,
    is_numeric_column as is_numeric_column_impl,
    validate_columns as validate_columns_impl,
    validate_combined_numeric_rules as validate_combined_numeric_rules_impl,
)


def excel_writer_engine() -> str:
    return excel_writer_engine_impl()


def auto_fit_columns(worksheet, df) -> None:
    auto_fit_columns_impl(worksheet, df)


def fields_label(fields: list[str]) -> str:
    return fields_label_impl(fields)


def normalize_fields(fields) -> list[str]:
    return normalize_fields_impl(fields)


def collect_rule_value(row, fields: list[str]):
    return collect_rule_value_impl(row, fields)


def compare_rule_values(file_1_row, file_2_row, file_1_fields, file_2_fields):
    return compare_rule_values_impl(file_1_row, file_2_row, file_1_fields, file_2_fields)


def aggregate_by_key(df, key_col: str):
    return aggregate_by_key_impl(df, key_col)


def read_excel_columns(path: Path, orientation: str = "vertical") -> list[str]:
    return read_excel_columns_impl(path, orientation=orientation)


def _validate_columns(df, columns, label: str) -> None:
    validate_columns_impl(df, columns, label)


def _is_numeric_column(series) -> bool:
    return is_numeric_column_impl(series)


def _validate_combined_numeric_rules(file_1_df, file_2_df, rules) -> None:
    validate_combined_numeric_rules_impl(file_1_df, file_2_df, rules)


def write_generic_report(output_path, reconciliation_results, file_1_not_found, file_2_not_found) -> None:
    write_generic_report_impl(output_path, reconciliation_results, file_1_not_found, file_2_not_found)


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
    progress_callback=None,
    file_1_name: str = "File 1",
    file_2_name: str = "File 2",
    is_cancelled=None,
) -> dict:
    return run_generic_reconciliation_impl(
        file_1_path,
        file_2_path,
        output_path,
        key_file_1,
        key_file_2,
        rules,
        orientation=orientation,
        include_columns_file_1=include_columns_file_1,
        include_columns_file_2=include_columns_file_2,
        progress_callback=progress_callback,
        file_1_name=file_1_name,
        file_2_name=file_2_name,
        is_cancelled=is_cancelled,
    )
