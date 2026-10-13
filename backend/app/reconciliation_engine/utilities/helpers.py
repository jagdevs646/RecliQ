from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Sequence

import pandas as pd

from app.reconciliation_engine.cache import is_blank, to_decimal, to_number


def collect_rule_value(
    row: dict,
    fields: list[str],
    number_format: str | None = None,
    known_matcher_type: str | None = None,
    combination_type: str | None = None,
) -> tuple[object, str | None]:
    values = [row[field] for field in fields if field in row]

    # The engine has already inferred this single column's type from both
    # files. Avoid reparsing every row just to rediscover the same type.
    if known_matcher_type is not None and len(values) == 1:
        value = values[0]
        if is_blank(value):
            return "", known_matcher_type
        if known_matcher_type == "numeric" or isinstance(value, (int, float, Decimal)):
            return value, known_matcher_type
        if isinstance(value, str):
            return value.strip(), known_matcher_type
        return str(value).strip(), known_matcher_type

    if len(values) > 1 and combination_type == "numeric":
        numbers = [to_decimal(value, number_format) for value in values]
        return sum((number or Decimal(0) for number in numbers), Decimal(0)), "numeric"

    if len(values) > 1 and combination_type == "text":
        text_values = [str(value).strip() for value in values if not is_blank(value)]
        return " ".join(text_values), "text"

    numbers = [to_decimal(value, number_format) for value in values]

    if values and all(number is not None or is_blank(value) for value, number in zip(values, numbers)):
        if len(values) > 1:
            return sum((number or Decimal(0) for number in numbers), Decimal(0)), "numeric"
        if numbers and numbers[0] is not None:
            return values[0], "numeric"

    text_values = [str(value).strip() for value in values if not is_blank(value)]
    return " ".join(text_values), None


def validate_columns(df: pd.DataFrame, columns: Iterable[str], label: str) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise KeyError(f"{label}: missing columns {', '.join(missing)}")


def is_numeric_column(series: pd.Series) -> bool:
    found_value = False
    for value in series:
        if is_blank(value):
            continue
        found_value = True
        if to_number(value) is None:
            return False
    return found_value


def _column_data_type(series: pd.Series) -> str | None:
    found_value = False
    for value in series:
        if is_blank(value):
            continue
        found_value = True
        if to_number(value) is None:
            return "text"
    return "numeric" if found_value else None


def _combined_column_type(df: pd.DataFrame, fields: list[str], side: str) -> str | None:
    if len(fields) < 2:
        return None

    field_types = {field: _column_data_type(df[field]) for field in fields}
    numeric_fields = [field for field, kind in field_types.items() if kind == "numeric"]
    text_fields = [field for field, kind in field_types.items() if kind == "text"]
    if numeric_fields and text_fields:
        raise ValueError(
            "Combined columns on each file must be all numeric (added together) or all text "
            "(joined with spaces). "
            f"{side} has numeric columns: {', '.join(numeric_fields)}; "
            f"text columns: {', '.join(text_fields)}."
        )
    if numeric_fields:
        return "numeric"
    if text_fields:
        return "text"
    return None


def infer_combined_column_types(
    file_1_df: pd.DataFrame,
    file_2_df: pd.DataFrame,
    rules: Iterable[tuple[list[str], list[str]]],
) -> list[tuple[str | None, str | None]]:
    """Infer how selected columns on each side are combined, independently."""
    inferred = []
    for file_1_fields, file_2_fields in rules:
        type_1 = _combined_column_type(file_1_df, file_1_fields, "File 1")
        type_2 = _combined_column_type(file_2_df, file_2_fields, "File 2")
        if type_1 and type_2 and type_1 != type_2:
            raise ValueError(
                "Combined mapping types must agree between files: "
                f"File 1 columns are {type_1}, while File 2 columns are {type_2}. "
                "Map numeric fields to numeric fields or text fields to text fields."
            )
        inferred.append((type_1, type_2))
    return inferred


def validate_combined_numeric_rules(
    file_1_df: pd.DataFrame,
    file_2_df: pd.DataFrame,
    rules: Iterable[tuple[list[str], list[str]]],
) -> None:
    # Kept as a backwards-compatible entry point for the reconciliation facade.
    infer_combined_column_types(file_1_df, file_2_df, rules)
