"""Suggest primary-key columns from the data, not from column names alone.

Suggestions are advisory: the UI offers them for one-click selection and never
applies them automatically. A pair of columns is a good key when its values are
unique within each file and the normalized values overlap across files.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

import pandas as pd

from app.reconciliation_engine.cache import normalize_text
from app.reconciliation_engine.matching.indexed_matcher import (
    detect_matcher_type,
    non_empty_values,
    normalized_identity_value,
)

# Rows sampled per file; key statistics stabilise well before this.
SAMPLE_ROWS = 2000
MIN_SUGGESTION_SCORE = 0.45

_ID_NAME = re.compile(r"\b(id|no|num|number|ref|reference|code|invoice|inv|voucher|bill|document|doc|gstin|pan|txn|transaction)\b")
_AMOUNT_NAME = re.compile(r"\b(amount|amt|total|value|tax|qty|quantity|price|rate|balance|debit|credit|net|gross|sum)\b")
_IDENTIFIER_TYPES = {"invoice", "gstin", "pan", "identifier"}


def _normalized_values(series: pd.Series, matcher_type: str) -> list[str]:
    return [
        value
        for value in (normalized_identity_value(item, matcher_type) for item in series.head(SAMPLE_ROWS))
        if value is not None
    ]


def detect_date_columns(df: pd.DataFrame) -> list[str]:
    """Columns whose values (not just names) are predominantly dates."""
    dates = []
    for column in df.columns:
        if str(column).startswith("__"):
            continue
        if pd.api.types.is_datetime64_any_dtype(df[column]):
            dates.append(column)
            continue
        sample = non_empty_values(df[column].head(500), limit=50)
        # Values decide; the column name is deliberately not used here.
        if sample and detect_matcher_type(sample, "", "") == "date":
            dates.append(column)
    return dates


def _candidate_pairs(df1: pd.DataFrame, df2: pd.DataFrame, mappings: Iterable[dict]) -> list[tuple[str, str]]:
    columns_2 = {normalize_text(column): column for column in df2.columns if not str(column).startswith("__")}
    pairs: list[tuple[str, str]] = []
    for column in df1.columns:
        if str(column).startswith("__"):
            continue
        match = columns_2.get(normalize_text(column))
        if match is not None:
            pairs.append((column, match))
    for mapping in mappings:
        source, target = mapping.get("source"), mapping.get("target")
        if target and mapping.get("confidence") in {"High", "Medium"} and source in df1.columns and target in df2.columns:
            pairs.append((source, target))
    return list(dict.fromkeys(pairs))


def _score_pair(df1: pd.DataFrame, df2: pd.DataFrame, source: str, destination: str, date_columns: set[str]) -> dict | None:
    sample_1, sample_2 = df1[source].head(SAMPLE_ROWS), df2[destination].head(SAMPLE_ROWS)
    completeness = (sample_1.notna().mean() + sample_2.notna().mean()) / 2
    if completeness < 0.9:
        return None
    matcher_type = detect_matcher_type(
        non_empty_values(list(sample_1.head(100)) + list(sample_2.head(100))), source, destination
    )
    values_1, values_2 = _normalized_values(sample_1, matcher_type), _normalized_values(sample_2, matcher_type)
    if not values_1 or not values_2:
        return None
    set_1, set_2 = set(values_1), set(values_2)
    uniqueness = (len(set_1) / len(values_1) + len(set_2) / len(values_2)) / 2
    overlap = len(set_1 & set_2) / min(len(set_1), len(set_2))
    score = uniqueness * 0.45 + overlap * 0.45 + completeness * 0.10

    names = f"{normalize_text(source)} {normalize_text(destination)}"
    is_date = matcher_type == "date" or source in date_columns
    is_amount = (matcher_type == "numeric" and not _ID_NAME.search(names)) or bool(_AMOUNT_NAME.search(names))
    if matcher_type in _IDENTIFIER_TYPES or _ID_NAME.search(names):
        score *= 1.2
    if is_amount:
        score *= 0.3  # Amounts are compared, never used to identify records.
    if is_date:
        score *= 0.5  # Many transactions share a date.
    return {
        "source": source,
        "destination": destination,
        "score": score,
        "uniqueness": uniqueness,
        "is_date": is_date,
        "is_amount": is_amount,
    }


def _composite_partner(df1: pd.DataFrame, df2: pd.DataFrame, best: dict, candidates: list[dict]) -> dict | None:
    """The partner column that best separates rows sharing the best key."""
    head = df1.head(SAMPLE_ROWS)
    best_partner, best_uniqueness = None, best["uniqueness"]
    for candidate in candidates:
        if candidate is best or candidate["is_amount"] or candidate["source"] == best["source"]:
            continue
        combined = head[[best["source"], candidate["source"]]].astype(str).agg("|".join, axis=1)
        uniqueness = combined.nunique() / max(len(combined), 1)
        if uniqueness > best_uniqueness + 0.02:
            best_partner, best_uniqueness = candidate, uniqueness
    return best_partner


def analyze_keys(df1: pd.DataFrame, df2: pd.DataFrame, mappings: Iterable[dict] = ()) -> Dict[str, Any]:
    """Recommend source/destination key columns and report date columns.

    Returns ``recommended_key`` (source columns) and
    ``recommended_key_destination`` (the paired destination columns, in order).
    """
    date_columns_1, date_columns_2 = detect_date_columns(df1), detect_date_columns(df2)
    result: Dict[str, Any] = {
        "recommended_key": [],
        "recommended_key_destination": [],
        "confidence": 0,
        "is_composite": False,
        "reason": "",
        "date_columns_1": date_columns_1,
        "date_columns_2": date_columns_2,
    }
    if df1.empty or df2.empty:
        return result

    candidates = [
        scored
        for source, destination in _candidate_pairs(df1, df2, mappings)
        if (scored := _score_pair(df1, df2, source, destination, set(date_columns_1))) is not None
    ]
    candidates.sort(key=lambda item: item["score"], reverse=True)
    if not candidates or candidates[0]["score"] < MIN_SUGGESTION_SCORE:
        return result

    best = candidates[0]
    keys: List[dict] = [best]
    if best["uniqueness"] < 0.95 or best["is_date"]:
        partner = _composite_partner(df1, df2, best, candidates[1:])
        if partner is not None:
            keys.append(partner)
            result["is_composite"] = True
            result["reason"] = f"{best['source']} repeats across rows; combining it with {partner['source']} identifies each record."
    result["recommended_key"] = [item["source"] for item in keys]
    result["recommended_key_destination"] = [item["destination"] for item in keys]
    result["confidence"] = min(100, int(best["score"] * 100))
    return result
