from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import numpy as np
import pandas as pd

from app.reconciliation_engine.cache import (
    compact_identifier,
    is_blank,
    normalize_text,
    parse_date_value,
    to_decimal,
)
from app.reconciliation_engine.normalization.entities import EntityNormalizer

try:
    from rapidfuzz import fuzz, process
except Exception:  # pragma: no cover - row comparison remains the fallback.
    fuzz = process = None


_TEXT_SCORERS = () if fuzz is None else (
    fuzz.WRatio,
    fuzz.token_sort_ratio,
    fuzz.token_set_ratio,
    fuzz.ratio,
    fuzz.partial_ratio,
)
_MAX_DEDUPLICATED_TEXT_PAIRS = 250_000


@dataclass(frozen=True)
class TextPairComparison:
    """Column-oriented comparison output aligned to a list of matched pairs."""

    equal: np.ndarray
    scores: np.ndarray
    normalization_rules: dict[int, tuple[str, ...]]
    values_1: list[str]
    values_2: list[str]


@dataclass(frozen=True)
class NumericPairComparison:
    """Exact numeric outcomes aligned to the matched-pair positions."""

    valid: np.ndarray
    equal: np.ndarray
    differences: np.ndarray


@dataclass(frozen=True)
class IdentifierPairComparison:
    """Exact identifier outcomes, including formatting-only confidence."""

    valid: np.ndarray
    confidence: np.ndarray


@dataclass(frozen=True)
class DatePairComparison:
    """Parsed date outcomes aligned to matched-pair positions."""

    valid: np.ndarray
    equal: np.ndarray


def compare_numeric_pairs(
    values_1: list[object],
    values_2: list[object],
    decimal_precision: int | None,
    number_format_1: str | None = None,
    number_format_2: str | None = None,
) -> NumericPairComparison | None:
    """Batch straightforward numeric pairs using the row engine's Decimal rules.

    Blanks and non-numeric values are marked invalid so callers can retain the
    existing row comparator for its blank-as-zero and type-inference behavior.
    """
    if len(values_1) != len(values_2) or not values_1 or decimal_precision is None:
        return None

    if (
        number_format_1 in (None, "decimal_point")
        and number_format_2 in (None, "decimal_point")
        and 0 <= decimal_precision <= 6
    ):
        array_1 = np.asarray(values_1)
        array_2 = np.asarray(values_2)
        if array_1.dtype.kind == "f" and array_2.dtype.kind == "f":
            finite = np.isfinite(array_1) & np.isfinite(array_2)
            scale = 10**decimal_precision
            scaled_1 = np.abs(array_1[finite]) * scale
            scaled_2 = np.abs(array_2[finite]) * scale
            if max(
                float(scaled_1.max(initial=0)),
                float(scaled_2.max(initial=0)),
            ) < 2**40:
                valid = finite
                equal = np.zeros(len(values_1), dtype=bool)
                differences = np.full(len(values_1), np.nan, dtype=np.float64)
                rounded_1 = np.copysign(np.floor(scaled_1 + 0.5), array_1[finite]).astype(np.int64)
                rounded_2 = np.copysign(np.floor(scaled_2 + 0.5), array_2[finite]).astype(np.int64)
                equal[finite] = rounded_1 == rounded_2

                # Binary floats can land just to either side of a decimal half;
                # let Decimal decide only those boundary cases.
                local_half_1 = np.abs((scaled_1 - np.floor(scaled_1)) - 0.5) <= 1e-3
                local_half_2 = np.abs((scaled_2 - np.floor(scaled_2)) - 0.5) <= 1e-3
                finite_positions = np.flatnonzero(finite)
                boundary_positions = finite_positions[local_half_1 | local_half_2]
                quantum = Decimal(1).scaleb(-decimal_precision)
                for index in boundary_positions:
                    number_1 = to_decimal(values_1[index], number_format_1)
                    number_2 = to_decimal(values_2[index], number_format_2)
                    if number_1 is None or number_2 is None:
                        valid[index] = False
                        continue
                    try:
                        comparable_1 = number_1.quantize(quantum, rounding=ROUND_HALF_UP)
                        comparable_2 = number_2.quantize(quantum, rounding=ROUND_HALF_UP)
                    except InvalidOperation:
                        valid[index] = False
                        continue
                    equal[index] = comparable_1 == comparable_2

                for index in np.flatnonzero(valid & ~equal):
                    differences[index] = float(
                        Decimal(str(values_1[index])) - Decimal(str(values_2[index]))
                    )
                return NumericPairComparison(valid, equal, differences)

    valid = np.zeros(len(values_1), dtype=bool)
    equal = np.zeros(len(values_1), dtype=bool)
    differences = np.full(len(values_1), np.nan, dtype=np.float64)
    quantum = Decimal(1).scaleb(-decimal_precision)

    for index, (value_1, value_2) in enumerate(zip(values_1, values_2)):
        if is_blank(value_1) or is_blank(value_2):
            continue
        number_1 = to_decimal(value_1, number_format_1)
        number_2 = to_decimal(value_2, number_format_2)
        if number_1 is None or number_2 is None:
            continue
        try:
            comparable_1 = number_1.quantize(quantum, rounding=ROUND_HALF_UP)
            comparable_2 = number_2.quantize(quantum, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            continue
        valid[index] = True
        equal[index] = comparable_1 == comparable_2
        if not equal[index]:
            differences[index] = float(number_1 - number_2)

    return NumericPairComparison(valid, equal, differences)


def compare_identifier_pairs(
    values_1: list[object],
    values_2: list[object],
) -> IdentifierPairComparison | None:
    """Batch exact identifier comparison without applying fuzzy matching."""
    if len(values_1) != len(values_2) or not values_1:
        return None

    valid = np.ones(len(values_1), dtype=bool)
    confidence = np.zeros(len(values_1), dtype=np.int16)
    for index, (value_1, value_2) in enumerate(zip(values_1, values_2)):
        if type(value_1) is type(value_2):
            try:
                if bool(value_1 == value_2):
                    confidence[index] = 100
                    continue
            except (TypeError, ValueError):
                pass
        compact_1 = compact_identifier(value_1)
        compact_2 = compact_identifier(value_2)
        if compact_1 != compact_2:
            continue
        confidence[index] = (
            100 if normalize_text(value_1) == normalize_text(value_2) else 97
        )
    return IdentifierPairComparison(valid, confidence)


def compare_date_pairs(
    values_1: list[object],
    values_2: list[object],
) -> DatePairComparison | None:
    """Batch valid dates while leaving malformed values to the row fallback."""
    if len(values_1) != len(values_2) or not values_1:
        return None

    valid = np.zeros(len(values_1), dtype=bool)
    equal = np.zeros(len(values_1), dtype=bool)
    for index, (value_1, value_2) in enumerate(zip(values_1, values_2)):
        date_1, date_2 = parse_date_value(value_1), parse_date_value(value_2)
        if date_1 is None or date_2 is None:
            continue
        valid[index] = True
        equal[index] = date_1 == date_2
    return DatePairComparison(valid, equal)


def compare_text_pairs(
    values_1: list[object],
    values_2: list[object],
    normalizer: EntityNormalizer,
) -> TextPairComparison | None:
    """Compare simple text pairs in batches; return None for mixed-type data.

    Equality is checked before normalization. Normal forms are built once per
    distinct value, and RapidFuzz scores only the remaining pairs.
    """
    if len(values_1) != len(values_2) or process is None or not values_1:
        return None
    if any(
        not isinstance(value, str) and not is_blank(value)
        for values in (values_1, values_2)
        for value in values
    ):
        return None

    left = ["" if is_blank(value) else value.strip() for value in values_1]
    right = ["" if is_blank(value) else value.strip() for value in values_2]
    left_array = np.asarray(left, dtype=object)
    right_array = np.asarray(right, dtype=object)
    equal = left_array == right_array
    scores = np.full(len(left), -1, dtype=np.int16)
    normalization_rules: dict[int, tuple[str, ...]] = {}

    forms: dict[str, tuple[str, str, str]] = {}

    def form(value: str) -> tuple[str, str, str]:
        cached = forms.get(value)
        if cached is None:
            normalized = normalize_text(value)
            identity = normalizer.identity(value) or ""
            canonical = normalizer.canonical(value).text or normalized
            cached = (normalized, identity, canonical)
            forms[value] = cached
        return cached

    fuzzy_pairs: list[int] = []
    for pair_index in np.flatnonzero(~equal):
        index = int(pair_index)
        value_1, value_2 = left[index], right[index]
        normalized_1, identity_1, _ = form(value_1)
        normalized_2, identity_2, _ = form(value_2)
        if normalized_1 == normalized_2 or (identity_1 and identity_1 == identity_2):
            equal[index] = True
            if normalized_1 != normalized_2:
                rules = tuple(normalizer.explain(value_1, value_2))
                if rules:
                    normalization_rules[index] = rules
        else:
            fuzzy_pairs.append(index)

    if fuzzy_pairs:
        canonical_1 = [form(left[index])[2] for index in fuzzy_pairs]
        canonical_2 = [form(right[index])[2] for index in fuzzy_pairs]

        # Reconciliation workbooks commonly repeat the same descriptions or
        # party names many times. Score each distinct canonical pair once, but
        # cap the cache so high-cardinality inputs do not trade CPU for a large
        # Python set. The uncached path remains the batch fallback.
        distinct_pairs: set[tuple[str, str]] = set()
        deduplicate = True
        for value_1, value_2 in zip(canonical_1, canonical_2):
            distinct_pairs.add((value_1, value_2))
            if len(distinct_pairs) > _MAX_DEDUPLICATED_TEXT_PAIRS:
                distinct_pairs.clear()
                deduplicate = False
                break

        deduplicate = deduplicate and len(distinct_pairs) < len(fuzzy_pairs)
        scored_pairs = list(distinct_pairs) if deduplicate else None
        if not deduplicate:
            distinct_pairs.clear()
        scoring_left = [pair[0] for pair in scored_pairs] if scored_pairs is not None else canonical_1
        scoring_right = [pair[1] for pair in scored_pairs] if scored_pairs is not None else canonical_2
        best = np.zeros(len(scoring_left), dtype=np.float64)
        for scorer in _TEXT_SCORERS:
            best = np.maximum(
                best,
                process.cpdist(
                    scoring_left,
                    scoring_right,
                    scorer=scorer,
                    workers=-1,
                    dtype=np.float64,
                ),
            )
        if deduplicate:
            assert scored_pairs is not None
            score_by_pair = {
                pair: 0 if not pair[0] or not pair[1] else min(int(best[index]), 99)
                for index, pair in enumerate(scored_pairs)
            }
            for pair_index, value_1, value_2 in zip(fuzzy_pairs, canonical_1, canonical_2):
                scores[pair_index] = score_by_pair[(value_1, value_2)]
        else:
            for position, pair_index in enumerate(fuzzy_pairs):
                score = (
                    0
                    if not canonical_1[position] or not canonical_2[position]
                    else min(int(best[position]), 99)
                )
                scores[pair_index] = score

    return TextPairComparison(equal, scores, normalization_rules, left, right)


def unique_exact_pairs(
    file_1: pd.DataFrame,
    file_2: pd.DataFrame,
    identity_columns_1: list[str],
    identity_columns_2: list[str],
) -> list[tuple[int, int]]:
    """Return positional pairs whose complete normalized keys are unique on both sides."""
    if not identity_columns_1 or len(identity_columns_1) != len(identity_columns_2):
        return []

    key_names = [f"__column_key_{index}" for index in range(len(identity_columns_1))]
    left = file_1[identity_columns_1].copy()
    right = file_2[identity_columns_2].copy()
    left.columns = key_names
    right.columns = key_names
    left["__source_position"] = range(len(left))
    right["__destination_position"] = range(len(right))

    left = left.dropna(subset=key_names)
    right = right.dropna(subset=key_names)
    left = left.loc[~left.duplicated(subset=key_names, keep=False)]
    right = right.loc[~right.duplicated(subset=key_names, keep=False)]
    if left.empty or right.empty:
        return []

    pairs = left.merge(
        right,
        on=key_names,
        how="inner",
        sort=False,
        validate="one_to_one",
    )
    pairs.sort_values("__source_position", kind="stable", inplace=True)
    return [
        (int(source_position), int(destination_position))
        for source_position, destination_position in pairs[
            ["__source_position", "__destination_position"]
        ].itertuples(index=False, name=None)
    ]
