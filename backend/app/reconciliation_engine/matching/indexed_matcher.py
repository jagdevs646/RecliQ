from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Sequence, Set, Tuple

import pandas as pd

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover
    fuzz = None

from app.reconciliation_engine.cache import (
    compact_identifier,
    is_blank,
    normalize_text,
    parse_date_value,
    sorted_token_key,
    to_number,
    tokens,
)

IDENTIFIER_NAME_HINTS = (
    "invoice",
    "inv",
    "bill",
    "voucher",
    "po",
    "purchase order",
    "order no",
    "challan",
    "reference",
    "ref",
    "document",
    "doc",
    "code",
    "id",
)

NUMERIC_NAME_HINTS = (
    "amount",
    "amt",
    "value",
    "tax",
    "igst",
    "cgst",
    "sgst",
    "cess",
    "debit",
    "credit",
    "balance",
    "qty",
    "quantity",
    "rate",
    "total",
)

DATE_NAME_HINTS = ("date", "dt", "period")
NAME_HINTS = ("name", "party", "vendor", "supplier", "customer", "trader", "firm", "company")


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    confidence: int
    matcher_type: str
    status: str
    detail: str = ""
    value1_normalized: str = ""
    value2_normalized: str = ""


def non_empty_values(values: Iterable[object], limit: int = 100) -> list[object]:
    result = []
    for value in values:
        if not is_blank(value):
            result.append(value)
        if len(result) >= limit:
            break
    return result


def _name_has_hint(column_name: str, hints: Sequence[str]) -> bool:
    name = normalize_text(column_name)
    return any(hint in name for hint in hints)


def _looks_like_gstin(value: object) -> bool:
    compact = compact_identifier(value).upper()
    return bool(re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]", compact))


def _looks_like_pan(value: object) -> bool:
    compact = compact_identifier(value).upper()
    return bool(re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", compact))


def _looks_like_identifier(value: object) -> bool:
    compact = compact_identifier(value)
    return bool(re.search(r"[a-z]", compact) and re.search(r"\d", compact))


def detect_matcher_type(
    values: Iterable[object] | None = None,
    column_name: str = "",
    other_column_name: str = "",
) -> str:
    combined_name = f"{column_name} {other_column_name}"

    if _name_has_hint(combined_name, ("gstin", "gst no", "gstn")):
        return "gstin"
    if _name_has_hint(combined_name, ("pan",)):
        return "pan"
    if _name_has_hint(combined_name, ("invoice", "inv")):
        return "invoice"
    if _name_has_hint(combined_name, DATE_NAME_HINTS):
        return "date"
    if _name_has_hint(combined_name, NUMERIC_NAME_HINTS):
        return "numeric"
    if _name_has_hint(combined_name, IDENTIFIER_NAME_HINTS):
        return "identifier"
    if _name_has_hint(combined_name, NAME_HINTS):
        return "company_name"

    sample = non_empty_values(values or [])
    if not sample:
        return "text"

    total = len(sample)
    numeric_count = sum(to_number(value) is not None for value in sample)
    date_count = sum(parse_date_value(value) is not None for value in sample)
    gstin_count = sum(_looks_like_gstin(value) for value in sample)
    pan_count = sum(_looks_like_pan(value) for value in sample)
    identifier_count = sum(_looks_like_identifier(value) for value in sample)

    if gstin_count / total >= 0.8:
        return "gstin"
    if pan_count / total >= 0.8:
        return "pan"
    if numeric_count / total >= 0.8:
        return "numeric"
    if date_count / total >= 0.8:
        return "date"
    if identifier_count / total >= 0.6:
        return "identifier"
    return "text"


def match_threshold(matcher_type: str) -> int:
    if matcher_type in {"numeric", "date", "invoice", "gstin", "pan", "identifier"}:
        return 95
    return 75


def _compare_numeric(value1: object, value2: object) -> MatchResult | None:
    num1 = to_number(value1)
    num2 = to_number(value2)
    if num1 is None or num2 is None:
        return None
    diff = round(num1 - num2, 2)
    if abs(diff) <= 0.005:
        return MatchResult(True, 100, "numeric", "Exact numeric match", value1_normalized=str(num1), value2_normalized=str(num2))
    return MatchResult(False, 0, "numeric", "Numeric difference", f"Difference: {diff}", str(num1), str(num2))


def _compare_date(value1: object, value2: object) -> MatchResult | None:
    date1 = parse_date_value(value1)
    date2 = parse_date_value(value2)
    if date1 is None or date2 is None:
        return None
    if date1 == date2:
        return MatchResult(True, 100, "date", "Exact date match", value1_normalized=date1.isoformat(), value2_normalized=date2.isoformat())
    return MatchResult(False, 0, "date", "Date mismatch", f"File1: {date1.isoformat()} | File2: {date2.isoformat()}", date1.isoformat(), date2.isoformat())


def _compare_identifier(value1: object, value2: object, matcher_type: str) -> MatchResult:
    raw1 = normalize_text(value1)
    raw2 = normalize_text(value2)
    compact1 = compact_identifier(value1)
    compact2 = compact_identifier(value2)

    if compact1 == "" and compact2 == "":
        return MatchResult(True, 100, matcher_type, "Both values blank")
    if compact1 == compact2:
        confidence = 100 if raw1 == raw2 else 97
        status = "Exact identifier match" if confidence == 100 else "Identifier formatting difference only"
        return MatchResult(True, confidence, matcher_type, status, value1_normalized=compact1, value2_normalized=compact2)
    return MatchResult(False, 0, matcher_type, "Identifier mismatch", f"File1: {value1} | File2: {value2}", compact1, compact2)


def _looks_like_name(value1: object, value2: object) -> bool:
    combined = f"{normalize_text(value1)} {normalize_text(value2)}"
    if re.search(r"\d", combined):
        return False
    word_count = len([word for word in combined.split() if word])
    return 4 <= word_count <= 10


def _difflib_score(value1: str, value2: str) -> int:
    from difflib import SequenceMatcher
    return int(round(SequenceMatcher(None, value1, value2).ratio() * 100))


def _fuzzy_score(value1: str, value2: str) -> int:
    if not value1 or not value2:
        return 0
    if fuzz is None:
        return _difflib_score(value1, value2)
    return int(
        max(
            fuzz.WRatio(value1, value2),
            fuzz.token_sort_ratio(value1, value2),
            fuzz.token_set_ratio(value1, value2),
            fuzz.ratio(value1, value2),
            fuzz.partial_ratio(value1, value2),
        )
    )


def _compare_text(value1: object, value2: object, matcher_type: str, prefer_name: bool) -> MatchResult:
    norm1 = normalize_text(value1)
    norm2 = normalize_text(value2)

    if norm1 == "" and norm2 == "":
        return MatchResult(True, 100, matcher_type, "Both values blank")
    if norm1 == norm2:
        return MatchResult(True, 100, matcher_type, "Exact text match", value1_normalized=norm1, value2_normalized=norm2)

    if prefer_name or _looks_like_name(value1, value2):
        key1 = sorted_token_key(value1)
        key2 = sorted_token_key(value2)
        if key1 and key1 == key2:
            return MatchResult(True, 100, matcher_type, "Name words reordered", value1_normalized=key1, value2_normalized=key2)

    synonym1 = normalize_text(value1, use_synonyms=True)
    synonym2 = normalize_text(value2, use_synonyms=True)
    if synonym1 == synonym2:
        return MatchResult(True, 100, matcher_type, "Business synonym match", value1_normalized=synonym1, value2_normalized=synonym2)

    synonym_key1 = sorted_token_key(value1, use_synonyms=True)
    synonym_key2 = sorted_token_key(value2, use_synonyms=True)
    if synonym_key1 and synonym_key1 == synonym_key2:
        return MatchResult(True, 100, matcher_type, "Business synonym/name order match", value1_normalized=synonym_key1, value2_normalized=synonym_key2)

    score = _fuzzy_score(synonym1 or norm1, synonym2 or norm2)
    if score >= 85:
        return MatchResult(True, score, matcher_type, "Minor spelling variation", value1_normalized=synonym1, value2_normalized=synonym2)
    if score >= 75:
        return MatchResult(True, score, matcher_type, "Possible Match - Manual Review", value1_normalized=synonym1, value2_normalized=synonym2)
    return MatchResult(False, score, matcher_type, "No Match", f"File1: {value1} | File2: {value2}", synonym1, synonym2)


def compare_values(
    value1: object,
    value2: object,
    column1: str = "",
    column2: str = "",
    matcher_type: str | None = None,
) -> MatchResult:
    matcher_type = matcher_type or detect_matcher_type([value1, value2], column1, column2)

    if matcher_type == "numeric":
        result = _compare_numeric(value1, value2)
        if result is not None:
            return result

    if matcher_type == "date":
        result = _compare_date(value1, value2)
        if result is not None:
            return result

    if matcher_type in {"invoice", "gstin", "pan", "identifier"}:
        return _compare_identifier(value1, value2, matcher_type)

    if matcher_type in {"person_name", "company_name"}:
        return _compare_text(value1, value2, matcher_type, prefer_name=True)

    return _compare_text(value1, value2, "text", prefer_name=False)


class IndexedCandidateMatcher:
    """
    High-performance O(1) hash indexed lookup candidate matcher for datasets.
    Replaces O(n^2) nested row scanning.
    """

    def __init__(self, candidates_df: pd.DataFrame, candidate_column: str, matcher_type: str):
        self.candidates_df = candidates_df
        self.candidate_column = candidate_column
        self.matcher_type = matcher_type
        self.threshold = match_threshold(matcher_type)

        self.rows: List[dict] = []
        self.indices: List[object] = []

        self.exact_map: Dict[str, List[int]] = {}
        self.compact_map: Dict[str, List[int]] = {}
        self.token_key_map: Dict[str, List[int]] = {}
        self.synonym_key_map: Dict[str, List[int]] = {}
        self.token_blocks: Dict[str, List[int]] = {}

        self._build_index()

    def _build_index(self) -> None:
        if self.candidate_column not in self.candidates_df.columns:
            return

        # Fast extraction using dict records
        records = self.candidates_df.to_dict('records')
        df_indices = self.candidates_df.index.tolist()

        for pos, (df_idx, row_dict) in enumerate(zip(df_indices, records)):
            self.rows.append(row_dict)
            self.indices.append(df_idx)

            raw_val = row_dict.get(self.candidate_column)

            if is_blank(raw_val):
                continue

            # 1. Exact normalized map
            norm = normalize_text(raw_val)
            if norm:
                self.exact_map.setdefault(norm, []).append(pos)

            # 2. Compact identifier map
            compact = compact_identifier(raw_val)
            if compact:
                self.compact_map.setdefault(compact, []).append(pos)

            # 3. Sorted token key map
            t_key = sorted_token_key(raw_val)
            if t_key:
                self.token_key_map.setdefault(t_key, []).append(pos)

            # 4. Synonym sorted token key map
            syn_key = sorted_token_key(raw_val, use_synonyms=True)
            if syn_key:
                self.synonym_key_map.setdefault(syn_key, []).append(pos)

            # 5. Token blocks for fallback scanning
            for t in tokens(raw_val, use_synonyms=True):
                self.token_blocks.setdefault(t, []).append(pos)

    def find_best_match(
        self,
        target_value: object,
        target_column: str = "",
        used_indices: Set[object] | None = None,
    ) -> Tuple[object | None, dict | None, MatchResult | None]:
        used_indices = used_indices or set()

        if is_blank(target_value) or not self.rows:
            return None, None, None

        norm_target = normalize_text(target_value)
        compact_target = compact_identifier(target_value)
        token_key_target = sorted_token_key(target_value)
        synonym_key_target = sorted_token_key(target_value, use_synonyms=True)

        candidate_positions: List[int] = []
        seen_pos: Set[int] = set()

        def add_candidates(positions: List[int] | None):
            if positions:
                for p in positions:
                    if p not in seen_pos:
                        seen_pos.add(p)
                        candidate_positions.append(p)

        # Probe maps in priority order
        if compact_target:
            add_candidates(self.compact_map.get(compact_target))
        if norm_target:
            add_candidates(self.exact_map.get(norm_target))
        if token_key_target:
            add_candidates(self.token_key_map.get(token_key_target))
        if synonym_key_target:
            add_candidates(self.synonym_key_map.get(synonym_key_target))

        # Check probed indexed candidates first
        best_pos = None
        best_result = None

        for pos in candidate_positions:
            df_idx = self.indices[pos]
            if df_idx in used_indices:
                continue

            row_val = self.rows[pos][self.candidate_column]
            result = compare_values(target_value, row_val, target_column, self.candidate_column, self.matcher_type)
            if result.confidence >= self.threshold and (best_result is None or result.confidence > best_result.confidence):
                best_pos = pos
                best_result = result
                if result.confidence == 100:
                    break

        if best_pos is not None and best_result is not None:
            return self.indices[best_pos], self.rows[best_pos], best_result

        # Fallback to fuzzy scanning ONLY if matcher_type requires text fuzzy match and exact lookups failed
        if self.matcher_type in {"text", "company_name", "person_name"}:
            # Limit scan to unused candidates that share at least one token
            fallback_candidates = set()
            for t in tokens(target_value, use_synonyms=True):
                if t in self.token_blocks:
                    for p in self.token_blocks[t]:
                        if p not in seen_pos:
                            fallback_candidates.add(p)

            for pos in fallback_candidates:
                df_idx = self.indices[pos]
                if df_idx in used_indices:
                    continue

                row_val = self.rows[pos].get(self.candidate_column)
                result = compare_values(target_value, row_val, target_column, self.candidate_column, self.matcher_type)
                if result.confidence >= self.threshold and (best_result is None or result.confidence > best_result.confidence):
                    best_pos = pos
                    best_result = result
                    if result.confidence == 100:
                        break

            if best_pos is not None and best_result is not None:
                return self.indices[best_pos], self.rows[best_pos], best_result

        return None, None, None


def best_match_for_value(
    target_value: object,
    candidates: pd.DataFrame,
    candidate_column: str,
    target_column: str = "",
    matcher_type: str | None = None,
    used_indices: set | None = None,
) -> tuple[int | None, dict | None, MatchResult | None]:
    used_indices = used_indices or set()
    candidate_values = list(candidates[candidate_column]) if candidate_column in candidates.columns else []
    matcher_type = matcher_type or detect_matcher_type([target_value, *candidate_values], target_column, candidate_column)

    indexed_matcher = IndexedCandidateMatcher(candidates, candidate_column, matcher_type)
    return indexed_matcher.find_best_match(target_value, target_column, used_indices)
