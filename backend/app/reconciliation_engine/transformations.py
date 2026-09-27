"""Configurable pre-match transformations.

A sheet rule can clean or reshape values before matching, for example turn
separate Debit/Credit columns into one signed amount, flip the sign of a
ledger that records the other side, or strip a prefix from invoice codes.

Only a fixed, safe set of operations exists: no expressions, no regular
expressions, nothing executable. Values used for matching are replaced in a
working copy; the original of every changed column is kept, and records in
the report show it next to the value that was matched.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from app.reconciliation_engine.cache import is_blank, normalize_header, to_number

ORIGINAL_PREFIX = "__ORIGINAL__"

TEXT_OPERATIONS = {
    "trim": "Trim spaces",
    "uppercase": "Upper case",
    "lowercase": "Lower case",
    "remove_characters": "Remove characters",
    "replace_text": "Replace text",
    "remove_prefix": "Remove prefix",
    "remove_suffix": "Remove suffix",
    "strip_leading_zeros": "Remove leading zeros",
    "keep_alphanumeric": "Keep letters and digits only",
}
NUMBER_OPERATIONS = {
    "invert_sign": "Invert sign",
    "absolute_value": "Absolute value",
    "multiply": "Multiply by",
    "round": "Round",
}
DERIVED_OPERATIONS = {"debit_credit_to_signed": "Debit/Credit → signed amount"}
SUPPORTED_OPERATIONS = {**TEXT_OPERATIONS, **NUMBER_OPERATIONS, **DERIVED_OPERATIONS}


@dataclass
class TransformationResult:
    df: pd.DataFrame
    applied: list[str] = field(default_factory=list)
    # transformed column -> column holding its original values
    originals: dict[str, str] = field(default_factory=dict)
    derived: list[str] = field(default_factory=list)


def _text_function(operation: str, params: dict[str, Any]) -> Callable[[Any], Any]:
    def text_only(transform: Callable[[str], str]) -> Callable[[Any], Any]:
        return lambda value: value if is_blank(value) else transform(str(value))

    if operation == "trim":
        return text_only(lambda value: " ".join(value.split()))
    if operation == "uppercase":
        return text_only(str.upper)
    if operation == "lowercase":
        return text_only(str.lower)
    if operation == "remove_characters":
        characters = set(str(params.get("characters", "")))
        return text_only(lambda value: "".join(character for character in value if character not in characters))
    if operation == "replace_text":
        find, replacement = str(params.get("find", "")), str(params.get("replace", ""))
        if not find:
            raise ValueError("Replace text needs the text to find.")
        return text_only(lambda value: value.replace(find, replacement))
    if operation in {"remove_prefix", "remove_suffix"}:
        affix = str(params.get("text", ""))
        if not affix:
            raise ValueError(f"{TEXT_OPERATIONS[operation]} needs the text to remove.")
        lowered = affix.lower()
        if operation == "remove_prefix":
            return text_only(lambda value: value[len(affix):] if value.lower().startswith(lowered) else value)
        return text_only(lambda value: value[: -len(affix)] if value.lower().endswith(lowered) else value)
    if operation == "strip_leading_zeros":
        return text_only(lambda value: value.lstrip("0") or ("0" if value else value))
    if operation == "keep_alphanumeric":
        return text_only(lambda value: "".join(character for character in value if character.isalnum()))
    raise ValueError(f"Unsupported text operation: {operation}")


def _number_function(operation: str, params: dict[str, Any]) -> Callable[[Any], Any]:
    def numeric(transform: Callable[[float], float]) -> Callable[[Any], Any]:
        def apply(value: Any) -> Any:
            number = to_number(value)
            return value if number is None else transform(number)

        return apply

    if operation == "invert_sign":
        return numeric(lambda number: -number)
    if operation == "absolute_value":
        return numeric(abs)
    if operation == "multiply":
        factor = to_number(params.get("factor"))
        if factor is None or not math.isfinite(factor):
            raise ValueError("Multiply needs a numeric factor.")
        return numeric(lambda number: number * factor)
    if operation == "round":
        decimals = int(params.get("decimals", 2))
        if not 0 <= decimals <= 6:
            raise ValueError("Round supports 0 to 6 decimal places.")
        return numeric(lambda number: round(number, decimals))
    raise ValueError(f"Unsupported number operation: {operation}")


def describe(step: dict[str, Any]) -> str:
    operation = step.get("operation", "")
    label = SUPPORTED_OPERATIONS.get(operation, operation)
    params = step.get("params") or {}
    if operation == "debit_credit_to_signed":
        sign = "debit positive" if params.get("debit_positive", True) else "credit positive"
        return f"{label}: {params.get('debit_column')} − {params.get('credit_column')} → {step.get('output_column')} ({sign})"
    detail = ""
    if operation == "replace_text":
        detail = f" '{params.get('find')}' → '{params.get('replace', '')}'"
    elif operation in {"remove_prefix", "remove_suffix"}:
        detail = f" '{params.get('text')}'"
    elif operation == "remove_characters":
        detail = f" '{params.get('characters')}'"
    elif operation == "multiply":
        detail = f" {params.get('factor')}"
    elif operation == "round":
        detail = f" to {params.get('decimals', 2)} decimals"
    return f"{label}{detail}: {', '.join(step.get('columns') or [])}"


def apply_transformations(df: pd.DataFrame, steps: list[dict[str, Any]] | None, side: str) -> TransformationResult:
    """Apply this side's steps, in order, to a copy of ``df``.

    ``side`` is "source" or "destination"; a step with side "both" applies to
    each file. Column names are matched after header normalization.
    """
    result = TransformationResult(df.copy())
    for step in steps or []:
        if step.get("side", "both") not in {side, "both"}:
            continue
        operation = step.get("operation", "")
        params = step.get("params") or {}
        if operation not in SUPPORTED_OPERATIONS:
            raise ValueError(f"Unsupported transformation: {operation}")

        if operation == "debit_credit_to_signed":
            debit, credit = normalize_header(params.get("debit_column", "")), normalize_header(params.get("credit_column", ""))
            output = normalize_header(step.get("output_column") or "SIGNED AMOUNT")
            missing = [column for column in (debit, credit) if column not in result.df.columns]
            if missing:
                raise ValueError(f"Transformation columns not found in {side} file: {', '.join(missing)}")
            if output in result.df.columns and output not in result.derived:
                raise ValueError(f"'{output}' already exists in the {side} file; choose another name for the signed amount.")
            sign = 1.0 if params.get("debit_positive", True) else -1.0

            def signed(row: pd.Series) -> float | None:
                debit_value, credit_value = to_number(row[debit]), to_number(row[credit])
                if debit_value is None and credit_value is None:
                    return None
                return sign * ((debit_value or 0.0) - (credit_value or 0.0))

            result.df[output] = result.df[[debit, credit]].apply(signed, axis=1)
            result.derived.append(output)
            result.applied.append(describe(step))
            continue

        columns = [normalize_header(column) for column in step.get("columns") or []]
        if not columns:
            raise ValueError(f"{SUPPORTED_OPERATIONS[operation]} needs at least one column.")
        missing = [column for column in columns if column not in result.df.columns]
        if missing:
            raise ValueError(f"Transformation columns not found in {side} file: {', '.join(missing)}")
        function = _text_function(operation, params) if operation in TEXT_OPERATIONS else _number_function(operation, params)
        for column in columns:
            original = f"{ORIGINAL_PREFIX}{column}"
            if column not in result.derived and original not in result.df.columns:
                result.df[original] = result.df[column]
                result.originals[column] = original
            result.df[column] = result.df[column].map(function)
        result.applied.append(describe(step))
    return result


def original_values(row: dict, originals: dict[str, str], suffix: str = "") -> dict[str, Any]:
    """'<column> (ORIGINAL)' entries for transformed values that changed."""
    values: dict[str, Any] = {}
    for column, original_column in originals.items():
        original, current = row.get(original_column), row.get(column)
        if not is_blank(original) and str(original) != str(current):
            values[f"{column}{suffix} (ORIGINAL)"] = original
    return values
