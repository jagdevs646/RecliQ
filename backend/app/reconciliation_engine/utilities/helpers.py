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
    values = [value for value in series if not is_blank(value)]
    return bool(values) and all(to_number(value) is not None for value in values)


def validate_combined_numeric_rules(
    file_1_df: pd.DataFrame,
    file_2_df: pd.DataFrame,
    rules: Iterable[tuple[list[str], list[str]]],
) -> None:
    for file_1_fields, file_2_fields in rules:
        if len(file_1_fields) == 1 and len(file_2_fields) == 1:
            continue

        invalid_file_1 = [field for field in file_1_fields if not is_numeric_column(file_1_df[field])]
        invalid_file_2 = [field for field in file_2_fields if not is_numeric_column(file_2_df[field])]
        if invalid_file_1 or invalid_file_2:
            details = []
            if invalid_file_1:
                details.append(f"File 1: {', '.join(invalid_file_1)}")
            if invalid_file_2:
                details.append(f"File 2: {', '.join(invalid_file_2)}")
            raise ValueError(
                "Combined column mappings require numeric columns. " + "; ".join(details)
            )
