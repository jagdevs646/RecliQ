"""Date-first, Decimal-safe reconciliation for bank and ledger data.

The matcher deliberately confirms only exact fingerprints, reciprocal unique
tolerance candidates, or uniquely supported one-to-many groups. Search is
restricted to same-day buckets for grouped transactions and bounded by a
candidate cap; unresolved possibilities remain visible for review.
"""
from __future__ import annotations

import itertools
import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

import pandas as pd

from app.reconciliation_engine.cache import detect_number_format, is_blank, normalize_header
from app.reconciliation_engine.matching.indexed_matcher import (
    detect_key_matcher_type,
    normalized_key_identity,
)

_DATE_SERIAL_ORIGIN = date(1899, 12, 30)
_MAX_GROUP_CANDIDATES = 20_000
_MAX_TOLERANCE_EDGES = 100_000
_CURRENCY_CODES = ("INR", "USD", "EUR", "GBP", "JPY", "CAD", "AUD", "SGD", "AED")
_CURRENCY_SYMBOLS = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
_CURRENCY_CODE_RE = re.compile(r"\b(" + "|".join(_CURRENCY_CODES) + r")\b", re.IGNORECASE)
_YEAR_FIRST_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T].*)?$")
_NUMERIC_DATE_RE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")


@dataclass(frozen=True)
class DateParseResult:
    values: tuple[date | None, ...]
    errors: dict[int, str]
    inferred_format: str | None


@dataclass(frozen=True)
class Transaction:
    position: int
    row: dict[str, Any]
    row_number: Any
    source_file: str
    source_sheet: str
    transaction_date: date
    amount: Decimal
    direction: str
    currency: str | None
    text_value: str | None
    text_normalized: str | None

    @property
    def period(self) -> str:
        return self.transaction_date.strftime("%Y-%m")

    @property
    def fingerprint(self) -> tuple[Any, ...]:
        return (self.transaction_date, self.direction, self.amount, self.currency, self.text_normalized)


def _date_clue(value: object) -> str | None:
    if is_blank(value) or isinstance(value, (date, datetime, pd.Timestamp)):
        return None
    match = _NUMERIC_DATE_RE.fullmatch(str(value).strip())
    if not match:
        return None
    first, second = int(match.group(1)), int(match.group(2))
    if first > 12 and second <= 12:
        return "day_first"
    if second > 12 and first <= 12:
        return "month_first"
    return None


def _excel_serial(value: object) -> date | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 1 or number > 2_958_465:
        return None
    try:
        return _DATE_SERIAL_ORIGIN + timedelta(days=int(number))
    except OverflowError:
        return None


def _parse_calendar_date(value: object, mode: str | None) -> tuple[date | None, str | None]:
    if is_blank(value):
        return None, "Date is blank."
    if isinstance(value, pd.Timestamp):
        return (None, "Date is invalid.") if pd.isna(value) else (value.date(), None)
    if isinstance(value, datetime):
        return value.date(), None
    if isinstance(value, date):
        return value, None
    serial = _excel_serial(value)
    if serial is not None:
        return serial, None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    compact = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    if compact:
        try:
            return date(*(int(part) for part in compact.groups())), None
        except ValueError:
            return None, "Date is not a valid calendar day."
    if re.fullmatch(r"\d{5,6}", text):
        serial_text = _excel_serial(int(text))
        if serial_text is not None:
            return serial_text, None
    year_first = _YEAR_FIRST_RE.fullmatch(text)
    if year_first:
        year, month, day = (int(part) for part in year_first.groups())
        try:
            return date(year, month, day), None
        except ValueError:
            return None, "Date is not a valid calendar day."
    numeric = _NUMERIC_DATE_RE.fullmatch(text)
    if numeric:
        first, second, year = (int(part) for part in numeric.groups())
        if mode is None and first <= 12 and second <= 12:
            return None, "Date is ambiguous; choose day-first or month-first for this file."
        if first > 12 and second > 12:
            return None, "Date is not a valid calendar day."
        day_first = mode != "month_first" if first <= 12 and second <= 12 else first > 12
        month, day = (second, first) if day_first else (first, second)
        try:
            return date(year, month, day), None
        except ValueError:
            return None, "Date is not a valid calendar day."
    try:
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=mode != "month_first")
    except (TypeError, ValueError, OverflowError):
        parsed = pd.NaT
    if pd.isna(parsed):
        return None, "Date could not be parsed."
    return parsed.date(), None


def parse_date_column(values, configured_format: str = "auto") -> DateParseResult:
    """Parse a date column without guessing when numeric dates remain ambiguous."""
    clues = {_date_clue(value) for value in values if _date_clue(value)}
    inferred = configured_format if configured_format != "auto" else (next(iter(clues)) if len(clues) == 1 else None)
    result: list[date | None] = []
    errors: dict[int, str] = {}
    for position, value in enumerate(values):
        parsed, error = _parse_calendar_date(value, inferred)
        result.append(parsed)
        if error:
            errors[position] = error
    return DateParseResult(tuple(result), errors, inferred)


def _normal_text(
    value: object,
    source_column: str = "",
    destination_column: str = "",
    matcher_type: str | None = None,
) -> str | None:
    if is_blank(value):
        return None
    return normalized_key_identity(value, source_column, destination_column, matcher_type)


def _currency_in_text(text: str) -> str | None:
    code = _CURRENCY_CODE_RE.search(text)
    if code:
        return code.group(1).upper()
    return next((currency for symbol, currency in _CURRENCY_SYMBOLS.items() if symbol in text), None)


def parse_financial_amount(value: object, number_format: str = "decimal_point") -> tuple[Decimal | None, str | None]:
    """Parse a money value with Decimal, preserving sign, parentheses and currency."""
    if is_blank(value):
        return None, None
    if isinstance(value, bool):
        return None, None
    if isinstance(value, Decimal):
        return (value, None) if value.is_finite() else (None, None)
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return None, None
        return Decimal(str(value)), None

    text = unicodedata.normalize("NFKC", str(value)).strip().replace("−", "-")
    currency = _currency_in_text(text)
    text = _CURRENCY_CODE_RE.sub("", text)
    for symbol in _CURRENCY_SYMBOLS:
        text = text.replace(symbol, "")
    text = text.replace("\u00a0", " ").replace("\u202f", " ").strip()
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    text = re.sub(r"[\s'’_]", "", text)
    sign = ""
    if text.startswith(("+", "-")):
        sign, text = text[0], text[1:]
    if not text or re.search(r"[^0-9,.]", text):
        return None, currency
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        tail = len(text) - text.rfind(",") - 1
        if number_format == "decimal_comma" or tail in (1, 2):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return None, currency
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None, currency
    if negative or sign == "-":
        amount = -amount
    return amount, currency


def _column_map(frame: pd.DataFrame) -> dict[str, str]:
    return {normalize_header(str(column)): str(column) for column in frame.columns}


def _infer_text_matcher_type(df1: pd.DataFrame, df2: pd.DataFrame, config: dict[str, Any]) -> str | None:
    source_column = str(config.get("text_source") or "")
    destination_column = str(config.get("text_destination") or "")
    if not source_column and not destination_column:
        return None
    values: list[object] = []
    for frame, selected in ((df1, source_column), (df2, destination_column)):
        actual = _column_map(frame).get(normalize_header(selected)) if selected else None
        if actual is not None:
            values.extend(value for value in frame[actual].head(500).tolist() if not is_blank(value))
    return detect_key_matcher_type(values[:200], source_column, destination_column)


def required_columns(config: dict[str, Any], side: str) -> list[str]:
    columns = [config.get(f"date_{side}")]
    mode = config.get(f"amount_mode_{side}", "single")
    columns.extend(
        [config.get(f"amount_{side}")]
        if mode == "single"
        else [config.get(f"debit_{side}"), config.get(f"credit_{side}")]
    )
    columns.append(config.get(f"text_{side}"))
    return list(dict.fromkeys(normalize_header(column) for column in columns if column))


def _issue(severity: str, category: str, message: str, *, side: str | None = None,
           column: str | None = None, count: int | None = None,
           examples: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"severity": severity, "category": category, "side": side, "column": column,
            "message": message, "count": count, "examples": examples or [], "suggestion": None}


def inspect_date_amount_inputs(df1: pd.DataFrame, df2: pd.DataFrame, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Data-quality checks shared by the precheck and the reconciliation path."""
    issues: list[dict[str, Any]] = []
    text_matcher_type = config.get("_text_matcher_type") or _infer_text_matcher_type(df1, df2, config)
    for side, frame in (("source", df1), ("destination", df2)):
        if frame.empty:
            issues.append(_issue("blocker", "empty", f"The {side} sheet has no data rows.", side=side))
            continue
        columns = _column_map(frame)
        missing = [column for column in required_columns(config, side) if column not in columns]
        if missing:
            issues.append(_issue("blocker", "missing_column", f"Column(s) not found in the {side} sheet: {', '.join(missing)}.", side=side, count=len(missing)))
            continue
        date_column = normalize_header(config[f"date_{side}"])
        date_result = parse_date_column(frame[columns[date_column]].tolist(), config.get(f"date_format_{side}", "auto"))
        ambiguous = [(index, reason) for index, reason in date_result.errors.items() if "ambiguous" in reason.casefold()]
        if ambiguous:
            issues.append(_issue(
                "blocker", "ambiguous_date_format",
                f"{len(ambiguous):,} {side} date(s) could mean either day/month or month/day. Choose the intended format for this file before continuing.",
                side=side, column=date_column, count=len(ambiguous),
                examples=[{"row": _row_number(frame.iloc[index], index), "value": str(frame.iloc[index][columns[date_column]])} for index, _ in ambiguous[:5]],
            ))
        invalid_dates = [(index, reason) for index, reason in date_result.errors.items() if "ambiguous" not in reason.casefold()]
        if invalid_dates:
            issues.append(_issue(
                "warning", "invalid_date",
                f"{len(invalid_dates):,} {side} date(s) cannot be used and will be listed for review.",
                side=side, column=date_column, count=len(invalid_dates),
                examples=[{"row": _row_number(frame.iloc[index], index), "value": str(frame.iloc[index][columns[date_column]])} for index, _ in invalid_dates[:5]],
            ))

        amount_columns = [normalize_header(config[f"amount_{side}"])] if config.get(f"amount_mode_{side}", "single") == "single" else [normalize_header(config[f"debit_{side}"]), normalize_header(config[f"credit_{side}"])]
        formats = {column: detect_number_format(frame[columns[column]].tolist())[0] for column in amount_columns}
        invalid_amounts: list[int] = []
        valid_amounts = False
        for index, row in enumerate(frame.to_dict("records")):
            parsed, _direction, _currency, error = _parse_row_amount(row, side, config, columns, formats)
            if error or parsed is None:
                invalid_amounts.append(index)
            else:
                valid_amounts = True
        if invalid_amounts:
            issues.append(_issue(
                "warning", "invalid_amount",
                f"{len(invalid_amounts):,} {side} amount(s) are blank, invalid or have conflicting debit/credit values; those rows will be listed for review.",
                side=side, column=" / ".join(amount_columns), count=len(invalid_amounts),
                examples=[{"row": _row_number(frame.iloc[index], index)} for index in invalid_amounts[:5]],
            ))
        if config.get(f"text_{side}"):
            text_column = columns[normalize_header(config[f"text_{side}"])]
            blank_indices = [
                index for index, value in enumerate(frame[text_column].tolist())
                if _normal_text(
                    value,
                    str(config.get("text_source") or ""),
                    str(config.get("text_destination") or ""),
                    text_matcher_type,
                ) is None
            ]
            if blank_indices:
                issues.append(_issue(
                    "warning", "missing_required_text",
                    f"{len(blank_indices):,} {side} row(s) have no value in the required matching-text column and will be listed for review.",
                    side=side, column=normalize_header(config[f"text_{side}"]), count=len(blank_indices),
                    examples=[{"row": _row_number(frame.iloc[index], index), "value": None} for index in blank_indices[:5]],
                ))
        if not any(value is not None for value in date_result.values) and not ambiguous:
            issues.append(_issue("blocker", "no_valid_dates", f"No usable dates were found in the {side} sheet.", side=side, column=date_column))
        if not valid_amounts:
            issues.append(_issue("warning", "no_valid_amounts", f"No usable amounts were found in the {side} sheet; rows will be listed for review.", side=side, column=" / ".join(amount_columns)))
    return issues


def _row_number(row: dict[str, Any], position: int) -> Any:
    for key in ("__SOURCE_ROW__", "_ROW_NO"):
        value = row.get(key)
        if value is not None and not is_blank(value):
            return value
    return position + 2


def _parse_row_amount(row: dict[str, Any], side: str, config: dict[str, Any], columns: dict[str, str], formats: dict[str, str]) -> tuple[Decimal | None, str | None, str | None, str | None]:
    mode = config.get(f"amount_mode_{side}", "single")
    override = config.get(f"currency_{side}") or None
    reverse = bool(config.get(f"reverse_direction_{side}", False))
    if mode == "single":
        selected = normalize_header(config.get(f"amount_{side}") or "")
        signed, currency = parse_financial_amount(row.get(columns.get(selected, "")), formats.get(selected, "decimal_point"))
        if signed is None:
            return None, None, None, "Amount is blank or invalid."
        if override and currency and override != currency:
            return None, None, currency, f"Amount currency {currency} conflicts with the selected {override} currency."
        currency = currency or override
        positive_direction = config.get(f"positive_is_{side}", "debit")
        direction = positive_direction if signed >= 0 else ("credit" if positive_direction == "debit" else "debit")
        if signed == 0:
            direction = "zero"
        amount = abs(signed)
    else:
        debit_column = normalize_header(config.get(f"debit_{side}") or "")
        credit_column = normalize_header(config.get(f"credit_{side}") or "")
        debit_value, debit_currency = parse_financial_amount(row.get(columns.get(debit_column, "")), formats.get(debit_column, "decimal_point"))
        credit_value, credit_currency = parse_financial_amount(row.get(columns.get(credit_column, "")), formats.get(credit_column, "decimal_point"))
        if debit_currency and credit_currency and debit_currency != credit_currency:
            return None, None, None, "Debit and credit cells use different currencies."
        currency = debit_currency or credit_currency
        if override and currency and override != currency:
            return None, None, currency, f"Amount currency {currency} conflicts with the selected {override} currency."
        currency = currency or override
        debit = debit_value or Decimal(0)
        credit = credit_value or Decimal(0)
        if debit and credit:
            return None, None, currency, "Both debit and credit are populated; the transaction direction is unclear."
        if debit_value is None and credit_value is None:
            return None, None, currency, "Debit and credit are both blank or invalid."
        if debit:
            amount = abs(debit)
            direction = "debit" if debit > 0 else "credit"
        elif credit:
            amount = abs(credit)
            direction = "credit" if credit > 0 else "debit"
        else:
            return None, None, currency, "Debit and credit are both zero; no transaction amount is available."
    if reverse:
        direction = "credit" if direction == "debit" else "debit" if direction == "credit" else direction
    return amount, direction, currency, None


def _build_transactions(frame: pd.DataFrame, side: str, config: dict[str, Any]) -> tuple[list[Transaction], list[dict[str, Any]], dict[str, Any]]:
    if frame.empty:
        raise ValueError(f"The {side} sheet has no data rows.")
    columns = _column_map(frame)
    missing = [column for column in required_columns(config, side) if column not in columns]
    if missing:
        raise ValueError(f"Column(s) not found in the {side} sheet: {', '.join(missing)}.")
    date_column = normalize_header(config[f"date_{side}"])
    parsed_dates = parse_date_column(frame[columns[date_column]].tolist(), config.get(f"date_format_{side}", "auto"))
    amount_columns = [normalize_header(config[f"amount_{side}"])] if config.get(f"amount_mode_{side}", "single") == "single" else [normalize_header(config[f"debit_{side}"]), normalize_header(config[f"credit_{side}"])]
    formats = {column: detect_number_format(frame[columns[column]].tolist())[0] for column in amount_columns}
    records = frame.to_dict("records")
    transactions: list[Transaction] = []
    invalid: list[dict[str, Any]] = []
    for position, row in enumerate(records):
        date_value = parsed_dates.values[position]
        date_error = parsed_dates.errors.get(position)
        amount, direction, currency, amount_error = _parse_row_amount(row, side, config, columns, formats)
        text_column = config.get(f"text_{side}")
        text_value = row.get(columns[normalize_header(text_column)]) if text_column else None
        text_normalized = _normal_text(
            text_value,
            str(config.get("text_source") or ""),
            str(config.get("text_destination") or ""),
            config.get("_text_matcher_type"),
        ) if text_column else None
        text_error = "Required matching text is blank." if text_column and text_normalized is None else None
        errors = [error for error in (date_error, amount_error, text_error) if error]
        source_file = str(row.get("__SOURCE_FILE__") or "")
        source_sheet = str(row.get("__SOURCE_SHEET__") or "")
        row_number = _row_number(row, position)
        if errors or date_value is None or amount is None or direction is None:
            invalid.append({
                "side": side,
                "position": position,
                "row": row,
                "row_number": row_number,
                "source_file": source_file,
                "source_sheet": source_sheet,
                "reason": "; ".join(errors) or "Required transaction values are unavailable.",
            })
            continue
        transactions.append(Transaction(
            position=position,
            row=row,
            row_number=row_number,
            source_file=source_file,
            source_sheet=source_sheet,
            transaction_date=date_value,
            amount=amount,
            direction=direction,
            currency=currency,
            text_value=str(text_value) if text_value is not None else None,
            text_normalized=text_normalized,
        ))
    return transactions, invalid, {
        "date_column": date_column,
        "format": parsed_dates.inferred_format,
        "valid_dates": sum(value is not None for value in parsed_dates.values),
    }


def _aggregate(transactions: list[Transaction], granularity: str) -> dict[tuple[Any, ...], dict[str, Any]]:
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in transactions:
        period = item.period if granularity == "month" else item.transaction_date.isoformat()
        key = (period, item.text_normalized, item.currency)
        bucket = groups.setdefault(key, {
            "period": period, "text_value": item.text_value, "currency": item.currency,
            "debit": Decimal(0), "credit": Decimal(0), "count": 0,
        })
        if item.direction == "debit":
            bucket["debit"] += item.amount
        elif item.direction == "credit":
            bucket["credit"] += item.amount
        bucket["count"] += 1
    return groups


def _number(value: Decimal) -> float:
    return float(value)


def _aggregate_rows(source: list[Transaction], destination: list[Transaction], granularity: str, summary_only: bool) -> list[dict[str, Any]]:
    source_groups, destination_groups = _aggregate(source, granularity), _aggregate(destination, granularity)
    all_keys = set(source_groups) | set(destination_groups)
    currencies_by_dimension: dict[tuple[str, str | None], set[str | None]] = defaultdict(set)
    for period, text_key, currency in all_keys:
        currencies_by_dimension[(period, text_key)].add(currency)
    currency_mismatch_dimensions = {dimension for dimension, currencies in currencies_by_dimension.items() if len(currencies) > 1}
    keys = sorted(all_keys, key=lambda key: (key[0], key[1] or "", key[2] or ""))
    result: list[dict[str, Any]] = []
    for period, text_key, currency in keys:
        left, right = source_groups.get((period, text_key, currency)), destination_groups.get((period, text_key, currency))
        left_debit = left["debit"] if left else Decimal(0)
        left_credit = left["credit"] if left else Decimal(0)
        right_debit = right["debit"] if right else Decimal(0)
        right_credit = right["credit"] if right else Decimal(0)
        equivalent_with_other_currency = (period, text_key) in currency_mismatch_dimensions
        if left is None or right is None:
            status = "Currency mismatch" if equivalent_with_other_currency else ("Only in source" if left else "Only in comparison")
        else:
            status = "Balanced" if left_debit == right_debit and left_credit == right_credit else "Difference"
        if summary_only and status == "Balanced":
            status = "Aggregate Balanced — Transactions Not Verified"
        month_start = period[:7] if granularity == "month" else period
        result.append({
            "Period": month_start,
            "Period label": datetime.strptime(period + ("-01" if granularity == "month" else ""), "%Y-%m-%d" if granularity == "month" else "%Y-%m-%d").strftime("%B %Y" if granularity == "month" else "%d %b %Y"),
            "Currency": currency or "Unspecified",
            "Matching text": (left or right or {}).get("text_value") if text_key is not None else "",
            "Source debit": _number(left_debit),
            "Comparison debit": _number(right_debit),
            "Debit difference": _number(left_debit - right_debit),
            "Source credit": _number(left_credit),
            "Comparison credit": _number(right_credit),
            "Credit difference": _number(left_credit - right_credit),
            "Source net": _number(left_debit - left_credit),
            "Comparison net": _number(right_debit - right_credit),
            "Source transactions": int((left or {}).get("count", 0)),
            "Comparison transactions": int((right or {}).get("count", 0)),
            "Aggregate status": status,
            "Transaction verification": "Not performed" if summary_only else "Completed",
        })
    return result


def _row_ref(item: Transaction) -> str:
    parts = [part for part in (item.source_file, item.source_sheet) if part]
    parts.append(f"row {item.row_number}")
    return " · ".join(parts)


def _record_context(item: Transaction, side: str, include_columns: list[str]) -> dict[str, Any]:
    context = {
        "Source file": item.source_file or None,
        "Source sheet": item.source_sheet or None,
        f"Original row in {side}": item.row_number,
    }
    for column in include_columns:
        normalized = normalize_header(column)
        actual = next((name for name in item.row if normalize_header(str(name)) == normalized), None)
        if actual is not None:
            context[column] = item.row.get(actual)
    return context


def _transaction_trace(items: list[Transaction], side: str, config: dict[str, Any]) -> str:
    """Keep selected original values beside every confirmed match group."""
    date_column = config.get(f"date_{side}")
    mode = config.get(f"amount_mode_{side}", "single")
    amount_columns = (
        [config.get(f"amount_{side}")]
        if mode == "single"
        else [config.get(f"debit_{side}"), config.get(f"credit_{side}")]
    )
    text_column = config.get(f"text_{side}")
    selected_columns = [column for column in [date_column, *amount_columns, text_column] if column]

    def display(value: Any) -> str:
        rendered = repr(value)
        return rendered if len(rendered) <= 900 else rendered[:897] + "..."

    details = []
    for item in items:
        originals = []
        for selected in selected_columns:
            actual = selected if selected in item.row else next(
                (name for name in item.row if normalize_header(str(name)) == normalize_header(str(selected))), None
            )
            if actual is not None:
                originals.append(f"{selected}={display(item.row.get(actual))}")
        signed = item.amount if item.direction == "debit" else -item.amount if item.direction == "credit" else Decimal(0)
        location = " · ".join(part for part in (item.source_file, item.source_sheet, f"row {item.row_number}") if part)
        details.append(
            f"{location}: {', '.join(originals)}; normalized={item.transaction_date.isoformat()}, "
            f"{signed} {item.currency or 'unspecified'} ({item.direction})"
            + (f", text={item.text_normalized!r}" if item.text_normalized is not None else "")
        )
    return "\n".join(details)


def _match_record(group_id: int, source: list[Transaction], destination: list[Transaction], method: str,
                  explanation: str, amount_difference: Decimal = Decimal(0), date_difference: int = 0,
                  source_name: str = "File 1", destination_name: str = "File 2",
                  include1: list[str] | None = None, include2: list[str] | None = None,
                  config: dict[str, Any] | None = None) -> dict[str, Any]:
    left_dates = sorted({item.transaction_date.isoformat() for item in source})
    right_dates = sorted({item.transaction_date.isoformat() for item in destination})
    left_amounts = [item.amount if item.direction == "debit" else -item.amount if item.direction == "credit" else Decimal(0) for item in source]
    right_amounts = [item.amount if item.direction == "debit" else -item.amount if item.direction == "credit" else Decimal(0) for item in destination]
    source_refs = [_row_ref(item) for item in source]
    destination_refs = [_row_ref(item) for item in destination]
    row = {
        "MATCH KEY": "; ".join(source_refs),
        "MATCHED KEY": "; ".join(destination_refs),
        "MATCH TYPE": method,
        "MATCH CONFIDENCE": "100%",
        "MATCH PASS": "Date & Amount · " + method,
        "MATCH EXPLANATION": explanation,
        "MATCH GROUP ID": f"DA-{group_id:06d}",
        "GROUP CLASSIFICATION": method,
        "GROUPED ROWS": f"{source_name}: {'; '.join(source_refs)} ↔ {destination_name}: {'; '.join(destination_refs)}",
        "ROW (FILE 1)": ", ".join(str(item.row_number) for item in source),
        "ROW (FILE 2)": ", ".join(str(item.row_number) for item in destination),
        "File 1 date": ", ".join(left_dates),
        "File 2 date": ", ".join(right_dates),
        "File 1 signed amount": _number(sum(left_amounts, Decimal(0))),
        "File 2 signed amount": _number(sum(right_amounts, Decimal(0))),
        "File 1 direction": ", ".join(sorted({item.direction for item in source})),
        "File 2 direction": ", ".join(sorted({item.direction for item in destination})),
        "Amount difference": _number(amount_difference),
        "Date difference (days)": date_difference,
        "Currency": (source[0].currency or destination[0].currency or "Unspecified"),
        "Normalized text": source[0].text_normalized if source and source[0].text_normalized is not None else "",
        "Source row count": len(source),
        "Comparison row count": len(destination),
        "File 1 transaction details": _transaction_trace(source, "source", config or {}),
        "File 2 transaction details": _transaction_trace(destination, "destination", config or {}),
        **(_record_context(source[0], source_name, include1 or []) if source else {}),
        **(_record_context(destination[0], destination_name, include2 or []) if destination else {}),
    }
    return row


def _candidate_edges(source: list[Transaction], destination: list[Transaction], amount_tolerance: Decimal,
                     date_tolerance: int) -> tuple[dict[int, dict[int, tuple[Decimal, int]]], bool]:
    by_identity: dict[tuple[str, str | None, str | None, date], list[Transaction]] = defaultdict(list)
    for item in destination:
        by_identity[(item.direction, item.text_normalized, item.currency, item.transaction_date)].append(item)
    result: dict[int, dict[int, tuple[Decimal, int]]] = {}
    edge_count = 0
    for left in source:
        matches: dict[int, tuple[Decimal, int]] = {}
        for offset in range(-date_tolerance, date_tolerance + 1):
            target_date = left.transaction_date + timedelta(days=offset)
            for right in by_identity.get((left.direction, left.text_normalized, left.currency, target_date), ()):
                amount_difference = abs(left.amount - right.amount)
                if amount_difference <= amount_tolerance:
                    matches[right.position] = (amount_difference, abs(offset))
                    edge_count += 1
                    if edge_count > _MAX_TOLERANCE_EDGES:
                        return {}, True
        if matches:
            result[left.position] = matches
    return result, False


def _unique_best(edges: dict[int, dict[int, tuple[Decimal, int]]], reverse: bool = False) -> dict[int, int | None]:
    grouped: dict[int, list[tuple[int, tuple[Decimal, int]]]] = defaultdict(list)
    for left, candidates in edges.items():
        for right, score in candidates.items():
            grouped[right if reverse else left].append((left if reverse else right, score))
    best: dict[int, int | None] = {}
    for key, candidates in grouped.items():
        minimum = min(score for _, score in candidates)
        winners = [candidate for candidate, score in candidates if score == minimum]
        best[key] = winners[0] if len(winners) == 1 else None
    return best


def _resolve_tolerance_pairs(source: list[Transaction], destination: list[Transaction],
                             amount_tolerance: Decimal, date_tolerance: int) -> tuple[list[tuple[Transaction, Transaction, Decimal, int]], dict[int, set[int]], set[int], set[int]]:
    remaining_source = {item.position: item for item in source}
    remaining_destination = {item.position: item for item in destination}
    accepted: list[tuple[Transaction, Transaction, Decimal, int]] = []
    final_edges: dict[int, set[int]] = {}
    capped_source: set[int] = set()
    capped_destination: set[int] = set()
    while remaining_source and remaining_destination and (amount_tolerance > 0 or date_tolerance > 0):
        edges, capped = _candidate_edges(list(remaining_source.values()), list(remaining_destination.values()), amount_tolerance, date_tolerance)
        if capped:
            capped_source.update(remaining_source)
            capped_destination.update(remaining_destination)
            break
        if not edges:
            break
        best_source = _unique_best(edges)
        best_destination = _unique_best(edges, reverse=True)
        pairs = [
            (left, right)
            for left, right in best_source.items()
            if right is not None and best_destination.get(right) == left
        ]
        if not pairs:
            final_edges = {left: set(candidates) for left, candidates in edges.items()}
            break
        for left, right in sorted(pairs):
            score = edges[left][right]
            accepted.append((remaining_source.pop(left), remaining_destination.pop(right), score[0], score[1]))
    if remaining_source and remaining_destination and (amount_tolerance > 0 or date_tolerance > 0):
        edges, capped = _candidate_edges(list(remaining_source.values()), list(remaining_destination.values()), amount_tolerance, date_tolerance)
        if capped:
            capped_source.update(remaining_source)
            capped_destination.update(remaining_destination)
        else:
            final_edges = {left: set(candidates) for left, candidates in edges.items()}
    return accepted, final_edges, capped_source, capped_destination


def _group_candidates(source: list[Transaction], destination: list[Transaction], max_size: int) -> tuple[list[tuple[tuple[int, ...], tuple[int, ...]]], bool, set[int], set[int]]:
    """Find exact same-day one-to-many/many-to-one subsets within a bounded search."""
    source_buckets: dict[tuple[Any, ...], list[Transaction]] = defaultdict(list)
    destination_buckets: dict[tuple[Any, ...], list[Transaction]] = defaultdict(list)
    for target, rows in ((source_buckets, source), (destination_buckets, destination)):
        for item in rows:
            target[(item.transaction_date, item.direction, item.currency, item.text_normalized)].append(item)
    candidates: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
    capped_source: set[int] = set()
    capped_destination: set[int] = set()
    capped = False
    for bucket in sorted(set(source_buckets) & set(destination_buckets), key=repr):
        left_rows = sorted(source_buckets[bucket], key=lambda item: item.position)
        right_rows = sorted(destination_buckets[bucket], key=lambda item: item.position)
        if not left_rows or not right_rows:
            continue
        generated = 0
        for many_rows, single_rows, many_on_source in ((right_rows, left_rows, False), (left_rows, right_rows, True)):
            by_amount: dict[Decimal, list[Transaction]] = defaultdict(list)
            for item in single_rows:
                if item.amount > 0:
                    by_amount[item.amount].append(item)
            max_subset = min(max_size, len(many_rows))
            for size in range(2, max_subset + 1):
                for subset in itertools.combinations(many_rows, size):
                    generated += 1
                    if generated > _MAX_GROUP_CANDIDATES:
                        return [], True, {item.position for item in source}, {item.position for item in destination}
                    total = sum((item.amount for item in subset), Decimal(0))
                    if not total:
                        continue
                    for single in by_amount.get(total, ()):
                        if many_on_source:
                            candidates.append((tuple(item.position for item in subset), (single.position,)))
                        else:
                            candidates.append(((single.position,), tuple(item.position for item in subset)))
                        if len(candidates) > _MAX_GROUP_CANDIDATES:
                            capped = True
                            capped_source.update(item.position for item in source)
                            capped_destination.update(item.position for item in destination)
                            return [], capped, capped_source, capped_destination
                if generated > _MAX_GROUP_CANDIDATES:
                    break
            if generated > _MAX_GROUP_CANDIDATES:
                break
        if generated > _MAX_GROUP_CANDIDATES:
            continue
    return candidates, capped, capped_source, capped_destination


def _review_record(side: str, item: Transaction, candidate_description: str, explanation: str) -> dict[str, Any]:
    return {
        "IDENTITY CLASSIFICATION": "AMBIGUOUS_MATCH",
        "MATCH KEY": _row_ref(item) if side == "source" else "",
        "CANDIDATE KEY": candidate_description if side == "source" else "",
        "MATCH CONFIDENCE": "0%",
        "MATCH PASS": "Date & Amount · manual review",
        "MATCH EXPLANATION": explanation,
        "ROW (FILE 1)": item.row_number if side == "source" else "",
        "ROW (FILE 2)": item.row_number if side == "destination" else "",
        "Source file": item.source_file or None,
        "Source sheet": item.source_sheet or None,
        "Transaction date": item.transaction_date.isoformat(),
        "Amount": _number(item.amount),
        "Direction": item.direction,
        "Currency": item.currency or "Unspecified",
        "Matching text": item.text_value,
    }


def _unmatched_record(item: Transaction, side: str, why: str, include_columns: list[str]) -> dict[str, Any]:
    record = {key: value for key, value in item.row.items() if not str(key).startswith("__")}
    record.update(_record_context(item, "File 1" if side == "source" else "File 2", include_columns))
    record.update({
        "Original row": item.row_number,
        "Transaction date (normalized)": item.transaction_date.isoformat(),
        "Amount (normalized)": _number(item.amount),
        "Direction (normalized)": item.direction,
        "Currency (normalized)": item.currency or "Unspecified",
        "Matching text (normalized)": item.text_normalized or "",
        "Why it was not matched": why,
    })
    return record


def _invalid_record(item: dict[str, Any], side: str, include_columns: list[str]) -> dict[str, Any]:
    row = {key: value for key, value in item["row"].items() if not str(key).startswith("__")}
    label = "File 1" if side == "source" else "File 2"
    for column in include_columns:
        actual = next((name for name in item["row"] if normalize_header(str(name)) == normalize_header(column)), None)
        if actual is not None:
            row[column] = item["row"].get(actual)
    row.update({
        "Source file": item["source_file"] or None,
        "Source sheet": item["source_sheet"] or None,
        "Original row": item["row_number"],
        "Why it was not matched": item["reason"],
        "ROW (FILE 1)" if side == "source" else "ROW (FILE 2)": item["row_number"],
        "IDENTITY CLASSIFICATION": "NOT_FOUND",
        "MATCH EXPLANATION": item["reason"],
        "MATCH KEY": f"{label} row {item['row_number']}",
    })
    return row


def reconcile_date_amount(
    df1: pd.DataFrame,
    df2: pd.DataFrame,
    config: dict[str, Any],
    *,
    file_1_name: str = "File 1",
    file_2_name: str = "File 2",
    include_columns_file_1: list[str] | None = None,
    include_columns_file_2: list[str] | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run monthly, daily and transaction checks; return the standard report contract."""
    report_progress = progress_callback or (lambda _percent, _message: None)
    check_cancel = is_cancelled or (lambda: False)
    config = {**config, "_text_matcher_type": _infer_text_matcher_type(df1, df2, config)}
    include1, include2 = include_columns_file_1 or [], include_columns_file_2 or []
    summary_only = not bool(config.get("verify_transactions", True))
    left, invalid_left, date_info_1 = _build_transactions(df1, "source", config)
    right, invalid_right, date_info_2 = _build_transactions(df2, "destination", config)
    for side, invalid, date_info in (
        ("source", invalid_left, date_info_1),
        ("destination", invalid_right, date_info_2),
    ):
        ambiguous_count = sum("ambiguous" in item["reason"].casefold() for item in invalid)
        if ambiguous_count:
            raise ValueError(
                f"{ambiguous_count:,} {side} date(s) could mean either day/month or month/day. "
                "Choose the intended format for this file before continuing."
            )
        if not date_info["valid_dates"]:
            raise ValueError(f"No usable dates were found in the {side} sheet.")
    report_progress(20, "Parsed dates and normalized exact decimal amounts")
    if check_cancel():
        raise InterruptedError("Reconciliation cancelled by user")

    monthly = _aggregate_rows(left, right, "month", summary_only)
    daily = _aggregate_rows(left, right, "day", summary_only)
    periods = {row["Period"] for row in monthly}
    balanced_group_statuses = {"Balanced", "Aggregate Balanced — Transactions Not Verified"}
    monthly_by_period: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in monthly:
        monthly_by_period[str(row["Period"])].append(row)
    balanced_periods = sum(all(row["Aggregate status"] in balanced_group_statuses for row in rows) for rows in monthly_by_period.values())
    balanced_groups = sum(row["Aggregate status"] in balanced_group_statuses for row in monthly)
    report_progress(35, "Compared year-month and daily debit/credit totals")

    matches: list[dict[str, Any]] = []
    review_records: list[dict[str, Any]] = []
    unmatched_source: list[Transaction] = []
    unmatched_destination: list[Transaction] = []
    source_matched_positions: set[int] = set()
    destination_matched_positions: set[int] = set()
    exact_duplicate_review: dict[tuple[str, int], str] = {}
    tolerance_candidate_edges: dict[int, set[int]] = {}
    group_review_source: set[int] = set()
    group_review_destination: set[int] = set()

    if not summary_only:
        source_by_key: dict[tuple[Any, ...], list[Transaction]] = defaultdict(list)
        destination_by_key: dict[tuple[Any, ...], list[Transaction]] = defaultdict(list)
        for item in left:
            source_by_key[item.fingerprint].append(item)
        for item in right:
            destination_by_key[item.fingerprint].append(item)
        for key in sorted(set(source_by_key) & set(destination_by_key), key=repr):
            left_rows = sorted(source_by_key[key], key=lambda item: item.position)
            right_rows = sorted(destination_by_key[key], key=lambda item: item.position)
            for source_item, destination_item in zip(left_rows, right_rows):
                source_matched_positions.add(source_item.position)
                destination_matched_positions.add(destination_item.position)
                matches.append(_match_record(
                    len(matches) + 1, [source_item], [destination_item], "Exact one-to-one",
                    "Normalized transaction date, amount, direction, currency and required text are identical.",
                    source_name=file_1_name, destination_name=file_2_name, include1=include1, include2=include2, config=config,
                ))
            if len(left_rows) != len(right_rows):
                message = f"This exact transaction occurs {len(left_rows)} time(s) in the source and {len(right_rows)} time(s) in the comparison file; duplicate counts differ."
                for item in left_rows[len(right_rows):]:
                    exact_duplicate_review[("source", item.position)] = message
                for item in right_rows[len(left_rows):]:
                    exact_duplicate_review[("destination", item.position)] = message

        remaining_left = [item for item in left if item.position not in source_matched_positions]
        remaining_right = [item for item in right if item.position not in destination_matched_positions]
        amount_tolerance = Decimal(str(config.get("amount_tolerance") or "0"))
        date_tolerance = int(config.get("date_tolerance_days") or 0)
        tolerance_matches, tolerance_candidate_edges, capped_tolerance_source, capped_tolerance_destination = _resolve_tolerance_pairs(
            remaining_left, remaining_right, amount_tolerance, date_tolerance,
        )
        for source_item, destination_item, amount_difference, date_difference in tolerance_matches:
            source_matched_positions.add(source_item.position)
            destination_matched_positions.add(destination_item.position)
            reasons = []
            if amount_difference:
                reasons.append(f"amount difference {amount_difference} within tolerance {amount_tolerance}")
            if date_difference:
                reasons.append(f"date difference {date_difference} day(s) within tolerance {date_tolerance}")
            matches.append(_match_record(
                len(matches) + 1, [source_item], [destination_item], "Tolerance one-to-one",
                "Accepted by explicit tolerance: " + " and ".join(reasons) + ".",
                amount_difference=amount_difference, date_difference=date_difference,
                source_name=file_1_name, destination_name=file_2_name, include1=include1, include2=include2, config=config,
            ))

        remaining_left = [item for item in left if item.position not in source_matched_positions]
        remaining_right = [item for item in right if item.position not in destination_matched_positions]
        tolerance_review_source = set(tolerance_candidate_edges) | capped_tolerance_source
        tolerance_review_destination = {
            position for candidates_for_source in tolerance_candidate_edges.values() for position in candidates_for_source
        } | capped_tolerance_destination
        if config.get("allow_group_matches", True):
            candidates, search_capped, capped_left, capped_right = _group_candidates(
                [item for item in remaining_left if item.position not in tolerance_review_source],
                [item for item in remaining_right if item.position not in tolerance_review_destination],
                int(config.get("max_group_size", 4)),
            )
            uses: dict[tuple[str, int], set[int]] = defaultdict(set)
            for candidate_index, (left_ids, right_ids) in enumerate(candidates):
                for position in left_ids:
                    uses[("source", position)].add(candidate_index)
                for position in right_ids:
                    uses[("destination", position)].add(candidate_index)
            accepted_candidates = [
                (left_ids, right_ids)
                for index, (left_ids, right_ids) in enumerate(candidates)
                if all(len(uses[("source", position)]) == 1 for position in left_ids)
                and all(len(uses[("destination", position)]) == 1 for position in right_ids)
            ]
            source_lookup = {item.position: item for item in remaining_left}
            destination_lookup = {item.position: item for item in remaining_right}
            for left_ids, right_ids in accepted_candidates:
                if any(position in source_matched_positions for position in left_ids) or any(position in destination_matched_positions for position in right_ids):
                    continue
                group_source = [source_lookup[position] for position in left_ids]
                group_destination = [destination_lookup[position] for position in right_ids]
                source_matched_positions.update(left_ids)
                destination_matched_positions.update(right_ids)
                method = "One-to-many" if len(left_ids) == 1 else "Many-to-one"
                matches.append(_match_record(
                    len(matches) + 1, group_source, group_destination, method,
                    f"Exact amount total, date, direction, currency and required text agree; the {len(left_ids)}-to-{len(right_ids)} grouping is the only supported candidate.",
                    source_name=file_1_name, destination_name=file_2_name, include1=include1, include2=include2, config=config,
                ))
            ambiguous_candidates = [
                (left_ids, right_ids)
                for index, (left_ids, right_ids) in enumerate(candidates)
                if any(len(uses[("source", position)]) > 1 for position in left_ids)
                or any(len(uses[("destination", position)]) > 1 for position in right_ids)
            ]
            for left_ids, right_ids in ambiguous_candidates:
                group_review_source.update(left_ids)
                group_review_destination.update(right_ids)
            if search_capped:
                group_review_source.update(capped_left)
                group_review_destination.update(capped_right)

        unmatched_source = [item for item in left if item.position not in source_matched_positions]
        unmatched_destination = [item for item in right if item.position not in destination_matched_positions]
        review_explanations: dict[tuple[str, int], tuple[str, str]] = {}
        source_by_position = {item.position: item for item in unmatched_source}
        destination_by_position = {item.position: item for item in unmatched_destination}
        for position in tolerance_review_source:
            item = source_by_position.get(position)
            if item is None:
                continue
            candidates = tolerance_candidate_edges.get(position, set())
            partners = [destination_by_position.get(candidate) for candidate in sorted(candidates)]
            names = [f"{file_2_name} row {partner.row_number}" for partner in partners if partner]
            if position in capped_tolerance_source:
                explanation = "The tolerance candidate search reached its safety limit; no transaction was selected automatically."
                names = ["Candidate search limit reached"]
            else:
                explanation = "Several tolerance candidates remain equally plausible; no transaction was selected automatically."
            review_explanations[("source", position)] = (", ".join(names), explanation)
        reverse_tolerance_edges: dict[int, set[int]] = defaultdict(set)
        for source_position, candidates in tolerance_candidate_edges.items():
            for destination_position in candidates:
                reverse_tolerance_edges[destination_position].add(source_position)
        for position in tolerance_review_destination:
            item = destination_by_position.get(position)
            if item is None:
                continue
            partners = [source_by_position.get(candidate) for candidate in sorted(reverse_tolerance_edges.get(position, set()))]
            names = [f"{file_1_name} row {partner.row_number}" for partner in partners if partner]
            explanation = "The tolerance candidate search reached its safety limit; no transaction was selected automatically." if position in capped_tolerance_destination else "Several tolerance candidates remain equally plausible; no transaction was selected automatically."
            if position in capped_tolerance_destination:
                names = ["Candidate search limit reached"]
            review_explanations[("destination", position)] = (", ".join(names), explanation)
        for side, items in (("source", unmatched_source), ("destination", unmatched_destination)):
            for item in items:
                key = (side, item.position)
                if key in exact_duplicate_review:
                    review_explanations[key] = ("Duplicate transaction", exact_duplicate_review[key])
                elif side == "source" and item.position in group_review_source:
                    review_explanations[key] = ("Possible transaction group", "More than one same-day transaction grouping has the same total; review the alternatives before confirming.")
                elif side == "destination" and item.position in group_review_destination:
                    review_explanations[key] = ("Possible transaction group", "More than one same-day transaction grouping has the same total; review the alternatives before confirming.")
                if key in review_explanations:
                    candidate, explanation = review_explanations[key]
                    review_records.append(_review_record(side, item, candidate, explanation))
        report_progress(78, "Verified unique transactions and checked bounded same-day groups")

    # The source and destination records are kept in separate result categories below.
    source_missing = [_invalid_record(item, "source", include1) for item in invalid_left]
    destination_missing = [_invalid_record(item, "destination", include2) for item in invalid_right]
    if not summary_only:
        amount_tol = Decimal(str(config.get("amount_tolerance") or "0"))
        date_tol = int(config.get("date_tolerance_days") or 0)
        for item in unmatched_source:
            reason = "No unique transaction counterpart met the selected date, amount, direction, currency and required-text rules."
            if ("source", item.position) in exact_duplicate_review:
                reason = exact_duplicate_review[("source", item.position)]
            elif item.position in group_review_source:
                reason = "A possible grouped match is ambiguous or exceeded the bounded search limit; review before confirming."
            elif item.position in tolerance_candidate_edges:
                reason = "More than one tolerance candidate remains plausible; no transaction was selected automatically."
            source_missing.append(_unmatched_record(item, "source", reason, include1))
        for item in unmatched_destination:
            reason = "No unique transaction counterpart met the selected date, amount, direction, currency and required-text rules."
            if ("destination", item.position) in exact_duplicate_review:
                reason = exact_duplicate_review[("destination", item.position)]
            elif item.position in group_review_destination:
                reason = "A possible grouped match is ambiguous or exceeded the bounded search limit; review before confirming."
            destination_missing.append(_unmatched_record(item, "destination", reason, include2))

    report_progress(88, "Preparing traceable result groups and exceptions")
    if check_cancel():
        raise InterruptedError("Reconciliation cancelled by user")
    verified_source_rows = sum(int(row["Source row count"]) for row in matches)
    verified_destination_rows = sum(int(row["Comparison row count"]) for row in matches)
    summary = {
        "report_rows": 0,
        "only_in_file_1": len(source_missing),
        "only_in_file_2": len(destination_missing),
        "confidence_review": len(review_records),
        "source_records": len(df1),
        "destination_records": len(df2),
        "matched_records": verified_source_rows,
        "fully_matched_records": verified_source_rows,
        "verified_match_groups": len(matches),
        "verified_matched_source_rows": verified_source_rows,
        "verified_matched_destination_rows": verified_destination_rows,
        "aggregate_periods": len(periods),
        "aggregate_balanced_months": balanced_periods,
        "monthly_summary_groups": len(monthly),
        "daily_periods": len({row["Period"] for row in daily}),
        "daily_summary_groups": len(daily),
        "aggregate_balanced_groups": balanced_groups,
        "aggregate_difference_groups": len(monthly) - balanced_groups,
        "source_records": len(df1),
        "comparison_records": len(df2),
        "source_unverified_rows": len(df1) - len(invalid_left) if summary_only else 0,
        "comparison_unverified_rows": len(df2) - len(invalid_right) if summary_only else 0,
        "exact_matches": sum(row["MATCH TYPE"] == "Exact one-to-one" for row in matches),
        "exception_matches": 0,
        "ambiguous_matches": len(review_records),
        "not_found_matches": len(source_missing) + len(destination_missing),
        "keyless_matches": len(matches),
        "field_discrepancies": 0,
        "date_amount_method": 1,
        "date_amount_summary_only": int(summary_only),
        "date_tolerance_days": int(config.get("date_tolerance_days") or 0),
        "amount_tolerance": float(Decimal(str(config.get("amount_tolerance") or "0"))),
    }
    if summary_only:
        summary["matched_records"] = 0
        summary["fully_matched_records"] = 0
        summary["verified_match_groups"] = 0
        summary["verified_matched_source_rows"] = 0
        summary["verified_matched_destination_rows"] = 0

    all_rows = [*left, *right]
    valid_periods = [item.transaction_date for item in all_rows]
    date_summary = {
        "summary_only": summary_only,
        "period_start": min(valid_periods).isoformat() if valid_periods else None,
        "period_end": max(valid_periods).isoformat() if valid_periods else None,
        "aggregate_periods": len(periods),
        "aggregate_balanced_months": balanced_periods,
        "monthly_summary_groups": len(monthly),
        "daily_periods": len({row["Period"] for row in daily}),
        "daily_summary_groups": len(daily),
        "aggregate_balanced_groups": balanced_groups,
        "aggregate_difference_groups": len(monthly) - balanced_groups,
        "source_records": len(df1),
        "comparison_records": len(df2),
        "source_unverified_rows": len(df1) - len(invalid_left) if summary_only else 0,
        "comparison_unverified_rows": len(df2) - len(invalid_right) if summary_only else 0,
        "verified_match_groups": len(matches),
        "verified_matched_source_rows": verified_source_rows if not summary_only else 0,
        "verified_matched_destination_rows": verified_destination_rows if not summary_only else 0,
        "source_invalid_rows": len(invalid_left),
        "comparison_invalid_rows": len(invalid_right),
        "amount_tolerance": str(config.get("amount_tolerance") or "0"),
        "date_tolerance_days": int(config.get("date_tolerance_days") or 0),
        "group_matches_enabled": bool(config.get("allow_group_matches", True)) and not summary_only,
        "date_format_source": date_info_1["format"] or "ambiguous or mixed; explicit values parsed where possible",
        "date_format_destination": date_info_2["format"] or "ambiguous or mixed; explicit values parsed where possible",
        "matching_rules": [
            "Date, debit/credit direction, currency and required text are hard constraints.",
            "Amount tolerance applies only to unique one-to-one pairs; grouped matches use exact totals.",
            "One-to-many and many-to-one groups are restricted to the same calendar day and must be unambiguous.",
        ],
    }
    universal_data = {
        "metadata": {
            "reconciliation_name": "Date & Amount Reconciliation",
            "file_1_name": file_1_name,
            "file_2_name": file_2_name,
            "matching_keys": ["Transaction date + amount"],
            "secondary_keys": [config.get("text_source")] if config.get("text_source") else [],
            "compared_fields": [],
            "date_amount": date_summary,
            "date_amount_config": {**config, "amount_tolerance": str(config.get("amount_tolerance") or "0")},
        },
        "statistics": {
            "total_file_1": len(df1),
            "total_file_2": len(df2),
            "matched": len(matches),
            "mismatched": 0,
            "missing_in_file_1": len(destination_missing),
            "missing_in_file_2": len(source_missing),
            "identity": {"EXACT_MATCH": len(matches), "EXCEPTION_MATCH": 0, "AMBIGUOUS_MATCH": len(review_records), "NOT_FOUND": len(source_missing) + len(destination_missing)},
        },
        "overall_status": "AGGREGATE ONLY" if summary_only else ("EXCEPTIONS FOUND" if source_missing or destination_missing or review_records else "PASSED"),
        "exception_summary": [],
        "field_exception_summary": [],
        "exceptions": [],
        "matched_records": matches,
        "missing_in_file_1": destination_missing,
        "missing_in_file_2": source_missing,
        "identity_resolution": review_records,
        "auto_resolved": [],
        "control_checks": [],
        "monthly_summary": monthly,
        "daily_summary": daily,
    }
    report_progress(100, "Date & Amount reconciliation complete")
    return {"summary": summary, "universal_data": universal_data}
