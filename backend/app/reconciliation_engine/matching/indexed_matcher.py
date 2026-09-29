from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from bisect import bisect_left, bisect_right
from typing import Dict, Iterable, List, Sequence, Set, Tuple

import pandas as pd

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover
    fuzz = None

from app.reconciliation_engine.normalization.entities import active_normalizer
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
    # Normalization rules that made the values comparable (e.g. "Pvt → Private").
    rules: tuple[str, ...] = ()


class MatchClassification(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    EXCEPTION_MATCH = "EXCEPTION_MATCH"
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
    NOT_FOUND = "NOT_FOUND"


def non_empty_values(values: Iterable[object], limit: int = 100) -> list[object]:
    result = []
    for value in values:
        if not is_blank(value):
            result.append(value)
        if len(result) >= limit:
            break
    return result


def _name_has_hint(column_name: str, hints: Sequence[str]) -> bool:
    """Short hints ("id", "pan", "po", "dt") must be whole words, so "Paid",
    "Company" or "Report" are not mistaken for IDs, PANs or POs."""
    name = normalize_text(column_name)
    words = set(name.split())
    return any(
        (hint in words) if len(hint) <= 4 and " " not in hint else (hint in name)
        for hint in hints
    )


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
    # "Invoice Date" is a date, not an invoice identifier.
    if re.search(r"\bdate\b", normalize_text(combined_name)):
        return "date"
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


def normalized_identity_value(value: object, matcher_type: str) -> str | None:
    """Return one deterministic, exact-match-safe identity component.

    This is intentionally stricter than ``compare_values``. Composite identity
    lookup must not turn a fuzzy text score into a record identity; each member
    must have a stable normalized representation before it can enter the index.
    """
    if is_blank(value):
        return None
    if matcher_type in {"invoice", "gstin", "pan", "identifier"}:
        normalized = compact_identifier(value)
    elif matcher_type == "date":
        parsed = parse_date_value(value)
        normalized = parsed.isoformat() if parsed else ""
    elif matcher_type == "numeric":
        number = to_number(value)
        normalized = str(number) if number is not None else ""
    else:
        # Names and text: deterministic business normalization ("ABC Pvt Ltd"
        # and "ABC PRIVATE LIMITED" share one identity). Never fuzzy.
        normalized = active_normalizer().identity(value) or ""
    return normalized or None


def composite_identity_key(values: Sequence[object], matcher_types: Sequence[str]) -> tuple[str, ...] | None:
    """Build a complete primary-key identity or reject an incomplete one."""
    if len(values) != len(matcher_types) or not values:
        return None
    components = [
        normalized_identity_value(value, matcher_type)
        for value, matcher_type in zip(values, matcher_types)
    ]
    if any(component is None for component in components):
        return None
    return tuple(component for component in components if component is not None)


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

    # Deterministic normalization first: legal forms, abbreviations, "&",
    # prefixes, initials, word order and organization aliases.
    normalizer = active_normalizer()
    identity1, identity2 = normalizer.identity(value1), normalizer.identity(value2)
    if identity1 and identity1 == identity2:
        rules = tuple(normalizer.explain(value1, value2))
        status = "Name words reordered" if rules == ("Word order ignored",) else "Business synonym match"
        return MatchResult(
            True, 100, matcher_type, status, "Normalized: " + ", ".join(rules), identity1, identity2, rules
        )

    # Fuzzy scoring only after every deterministic rule failed; scores below
    # 85 are flagged for manual review and never count as exact. A fuzzy score
    # is at most 99: subset scorers give 100 when one text contains the other
    # ("Automobiles" in "Automobiles & Components"), and 100 means identical.
    canonical1 = normalizer.canonical(value1).text or norm1
    canonical2 = normalizer.canonical(value2).text or norm2
    score = min(_fuzzy_score(canonical1, canonical2), 99)
    if score >= 85:
        return MatchResult(True, score, matcher_type, "Minor spelling variation", value1_normalized=canonical1, value2_normalized=canonical2)
    if score >= 75:
        return MatchResult(True, score, matcher_type, "Possible Match - Manual Review", value1_normalized=canonical1, value2_normalized=canonical2)
    return MatchResult(False, score, matcher_type, "No Match", f"File1: {value1} | File2: {value2}", canonical1, canonical2)


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

    def __init__(
        self,
        candidates_df: pd.DataFrame,
        candidate_column: str | Sequence[str],
        matcher_type: str | Sequence[str],
    ):
        self.candidates_df = candidates_df
        self.candidate_columns = [candidate_column] if isinstance(candidate_column, str) else list(candidate_column)
        if not self.candidate_columns:
            raise ValueError("At least one candidate key column is required")
        matcher_types = [matcher_type] if isinstance(matcher_type, str) else list(matcher_type)
        if len(matcher_types) == 1 and len(self.candidate_columns) > 1:
            matcher_types *= len(self.candidate_columns)
        if len(matcher_types) != len(self.candidate_columns):
            raise ValueError("Each candidate key column requires a matcher type")
        self.component_matcher_types = matcher_types
        self.is_composite = len(self.candidate_columns) > 1
        self.candidate_column = self.candidate_columns[0]
        self.matcher_type = "composite" if self.is_composite else matcher_types[0]
        self.threshold = match_threshold(self.matcher_type)

        self.rows: List[dict] = []
        self.indices: List[object] = []

        self.exact_map: Dict[str, List[int]] = {}
        self.compact_map: Dict[str, List[int]] = {}
        self.token_key_map: Dict[str, List[int]] = {}
        self.synonym_key_map: Dict[str, List[int]] = {}
        self.token_blocks: Dict[str, List[int]] = {}
        self.date_map: Dict[str, List[int]] = {}
        self.composite_map: Dict[tuple[str, ...], List[int]] = {}
        self.identity_map: Dict[str, List[int]] = {}
        self.condition_exact_maps: Dict[str, Dict[str, List[int]]] = {}
        self.condition_date_maps: Dict[str, Dict[str, List[int]]] = {}
        self.condition_numeric_values: Dict[str, List[tuple[float, int]]] = {}
        self.condition_token_blocks: Dict[str, Dict[str, List[int]]] = {}

        self._build_index()

    def _build_index(self) -> None:
        if any(column not in self.candidates_df.columns for column in self.candidate_columns):
            return

        # Fast extraction using dict records
        records = self.candidates_df.to_dict('records')
        df_indices = self.candidates_df.index.tolist()

        for pos, (df_idx, row_dict) in enumerate(zip(df_indices, records)):
            self.rows.append(row_dict)
            self.indices.append(df_idx)

            if self.is_composite:
                identity = composite_identity_key(
                    [row_dict.get(column) for column in self.candidate_columns],
                    self.component_matcher_types,
                )
                if identity is not None:
                    self.composite_map.setdefault(identity, []).append(pos)
                continue

            raw_val = row_dict.get(self.candidate_column)

            if is_blank(raw_val):
                continue

            identity = normalized_identity_value(raw_val, self.matcher_type)
            if identity:
                self.identity_map.setdefault(identity, []).append(pos)

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

            # 6. Date map
            if self.matcher_type == "date":
                date_val = parse_date_value(raw_val)
                if date_val:
                    self.date_map.setdefault(date_val.isoformat(), []).append(pos)

    def find_exact_candidates(
        self,
        target_value: object | Sequence[object],
        used_indices: Set[object] | None = None,
    ) -> list[tuple[object, dict, MatchResult]]:
        """Return all unused exact normalized primary-key candidates.

        The caller, not this low-level index, decides whether multiple exact
        candidates are an ambiguity. This prevents accidental first-row wins.
        """
        used_indices = used_indices or set()
        if self.is_composite:
            values = list(target_value) if isinstance(target_value, (list, tuple)) else [target_value]
            identity = composite_identity_key(values, self.component_matcher_types)
            if identity is None:
                return []
            positions = self.composite_map.get(identity, [])
            normalized_value = " | ".join(identity)
            result = MatchResult(
                True,
                100,
                "composite",
                "Exact composite key match",
                f"Exact normalized match across {len(self.candidate_columns)} primary-key columns",
                normalized_value,
                normalized_value,
            )
        else:
            identity = normalized_identity_value(target_value, self.matcher_type)
            if identity is None:
                return []
            positions = self.identity_map.get(identity, [])
            result = MatchResult(
                True,
                100,
                self.matcher_type,
                "Exact normalized primary-key match",
                value1_normalized=identity,
                value2_normalized=identity,
            )

        return [
            (self.indices[position], self.rows[position], result)
            for position in positions
            if self.indices[position] not in used_indices
        ]

    def token_candidate_indices(self, target_value: object | Sequence[object]) -> Set[object]:
        """Rows sharing a primary-key token with the target (bounded fuzzy scope).

        Composite identities have no token index: they only match exactly.
        """
        if self.is_composite or is_blank(target_value):
            return set()
        positions: Set[int] = set(self.compact_map.get(compact_identifier(target_value), []))
        for token in tokens(target_value, use_synonyms=True):
            positions.update(self.token_blocks.get(token, []))
        return {self.indices[position] for position in positions}

    def _condition_exact_map(self, column: str) -> Dict[str, List[int]]:
        if column not in self.condition_exact_maps:
            index: Dict[str, List[int]] = {}
            normalizer = active_normalizer()
            for position, row in enumerate(self.rows):
                value = normalizer.identity(row.get(column))
                if value:
                    index.setdefault(value, []).append(position)
            self.condition_exact_maps[column] = index
        return self.condition_exact_maps[column]

    def _condition_date_map(self, column: str) -> Dict[str, List[int]]:
        if column not in self.condition_date_maps:
            index: Dict[str, List[int]] = {}
            for position, row in enumerate(self.rows):
                parsed = parse_date_value(row.get(column))
                if parsed:
                    index.setdefault(parsed.isoformat(), []).append(position)
            self.condition_date_maps[column] = index
        return self.condition_date_maps[column]

    def _condition_numeric_index(self, column: str) -> List[tuple[float, int]]:
        if column not in self.condition_numeric_values:
            values = [
                (number, position)
                for position, row in enumerate(self.rows)
                if (number := to_number(row.get(column))) is not None
            ]
            self.condition_numeric_values[column] = sorted(values)
        return self.condition_numeric_values[column]

    def _condition_token_blocks(self, column: str) -> Dict[str, List[int]]:
        if column not in self.condition_token_blocks:
            blocks: Dict[str, List[int]] = {}
            for position, row in enumerate(self.rows):
                for token in tokens(row.get(column), use_synonyms=True):
                    blocks.setdefault(token, []).append(position)
            self.condition_token_blocks[column] = blocks
        return self.condition_token_blocks[column]

    def _candidate_positions_for_condition(self, condition: dict) -> tuple[Set[int], str]:
        source_value = condition.get("source_value")
        destination_column = str(condition.get("destination_column", ""))
        method = str(condition.get("comparison_method", ""))
        if not destination_column or destination_column not in self.candidates_df.columns:
            return set(), f"{destination_column or 'destination column'} is unavailable"

        if method == "exact_text":
            # "Exact" after deterministic normalization, never fuzzy.
            value = active_normalizer().identity(source_value)
            positions = set(self._condition_exact_map(destination_column).get(value, [])) if value else set()
            return positions, f"{destination_column} exact text"

        if method == "normalized_date":
            parsed = parse_date_value(source_value)
            positions = set(self._condition_date_map(destination_column).get(parsed.isoformat(), [])) if parsed else set()
            return positions, f"{destination_column} normalized date"

        if method == "numeric_tolerance":
            number = to_number(source_value)
            tolerance = float(condition.get("numeric_tolerance") or 0)
            if number is None:
                return set(), f"{destination_column} numeric value is invalid"
            numeric_index = self._condition_numeric_index(destination_column)
            left = bisect_left(numeric_index, (number - tolerance, -1))
            right = bisect_right(numeric_index, (number + tolerance, float("inf")))
            return {position for _, position in numeric_index[left:right]}, f"{destination_column} within {tolerance:g}"

        if method == "matcher_based":
            candidate_positions: Set[int] = set()
            for token in tokens(source_value, use_synonyms=True):
                candidate_positions.update(self._condition_token_blocks(destination_column).get(token, []))
            valid_positions = {
                position
                for position in candidate_positions
                if compare_values(
                    source_value,
                    self.rows[position].get(destination_column),
                    "",
                    destination_column,
                ).matched
            }
            return valid_positions, f"{destination_column} explicit matcher"

        return set(), f"Unsupported comparison method: {method}"

    def find_secondary_candidates(
        self,
        source_row: dict,
        conditions: Sequence[dict],
        used_indices: Set[object] | None = None,
    ) -> tuple[list[tuple[object, dict]], list[str]]:
        """Narrow candidates with every configured secondary condition.

        Each condition uses an index; explicit fuzzy matching is limited to the
        token block produced by that condition. Conditions are ANDed, never ORed.
        """
        used_indices = used_indices or set()
        method_order = {
            "exact_text": 0,
            "normalized_date": 1,
            "numeric_tolerance": 2,
            "matcher_based": 3,
        }
        positions: Set[int] | None = None
        details: list[str] = []
        for configured_condition in sorted(
            conditions,
            key=lambda condition: method_order.get(str(condition.get("comparison_method", "")), 99),
        ):
            source_column = str(configured_condition.get("source_column", ""))
            condition = dict(configured_condition)
            condition["source_value"] = source_row.get(source_column)
            matches, detail = self._candidate_positions_for_condition(condition)
            details.append(detail)
            positions = matches if positions is None else positions.intersection(matches)
            if not positions:
                return [], details

        if positions is None:
            return [], details
        return [
            (self.indices[position], self.rows[position])
            for position in sorted(positions)
            if self.indices[position] not in used_indices
        ], details

    def find_best_match(
        self,
        target_value: object | Sequence[object],
        target_column: str | Sequence[str] = "",
        used_indices: Set[object] | None = None,
    ) -> Tuple[object | None, dict | None, MatchResult | None]:
        used_indices = used_indices or set()

        if not self.rows:
            return None, None, None

        if self.is_composite:
            exact_candidates = self.find_exact_candidates(target_value, used_indices)
            if not exact_candidates:
                return None, None, None
            return exact_candidates[0]

        if is_blank(target_value):
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

        date_target = None
        if self.matcher_type == "date":
            parsed = parse_date_value(target_value)
            if parsed:
                date_target = parsed.isoformat()

        # Probe maps in priority order
        if date_target:
            add_candidates(self.date_map.get(date_target))
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
