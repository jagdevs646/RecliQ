"""Ordered keyless matching passes.

After the primary-key pass, each configured pass looks only at records that
are still unmatched on both sides:

* ``amount_date``      exact amount and a date within ±N days;
* ``amount_tolerance`` amount within an absolute and/or percentage tolerance,
                       optionally with a date window;
* either pass can also require a similar narrative/reference (fuzzy score on
  normalized text, with a minimum score).

A pair is matched only when it is *mutually unique*: the source record has
exactly one qualifying candidate, and that candidate qualifies for no other
unmatched source record. Anything else is reported as ambiguous and left for
a person to decide; a pass never picks between equal candidates.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Callable, Sequence

from app.reconciliation_engine.cache import is_blank, parse_date_value, to_number
from app.reconciliation_engine.normalization.entities import active_normalizer

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover
    fuzz = None

PASS_TYPES = {"amount_date": "Amount + date", "amount_tolerance": "Amount within tolerance"}
# Above this many candidates in an amount range a source record cannot be
# resolved uniquely; it is reported as ambiguous without scoring each one.
_MAX_CANDIDATES = 5000


@dataclass(frozen=True)
class PassMatch:
    source_position: int
    destination_position: int
    detail: str
    exact: bool


@dataclass(frozen=True)
class PassAmbiguity:
    source_position: int
    candidate_count: int
    detail: str


def pass_label(config: dict, number: int) -> str:
    if config.get("name"):
        return str(config["name"])
    label = PASS_TYPES.get(config.get("type", ""), "Matching pass")
    window = int(config.get("date_window_days") or 0)
    if config.get("date_source") and window:
        label += f" ±{window} day{'s' if window != 1 else ''}"
    if config.get("narrative_source"):
        label += " + reference"
    return f"Pass {number}: {label}"


def validate_pass(config: dict) -> None:
    pass_type = config.get("type")
    if pass_type not in PASS_TYPES:
        raise ValueError(f"Unknown matching pass type: {pass_type!r}")
    if not config.get("amount_source") or not config.get("amount_destination"):
        raise ValueError("A matching pass needs an amount column in each file.")
    if pass_type == "amount_date" and not (config.get("date_source") and config.get("date_destination")):
        raise ValueError("An amount + date pass needs a date column in each file.")
    if bool(config.get("narrative_source")) != bool(config.get("narrative_destination")):
        raise ValueError("Choose a reference/narrative column in both files, or in neither.")
    if not 0 <= int(config.get("date_window_days") or 0) <= 366:
        raise ValueError("The date window must be between 0 and 366 days.")
    if float(config.get("amount_tolerance") or 0) < 0 or float(config.get("amount_tolerance_percent") or 0) < 0:
        raise ValueError("Amount tolerances cannot be negative.")
    if not 0 <= int(config.get("narrative_threshold", 85)) <= 100:
        raise ValueError("The reference similarity threshold must be between 0 and 100.")


def _narrative_score(value_1: object, value_2: object) -> int:
    if is_blank(value_1) or is_blank(value_2):
        return 0
    normalizer = active_normalizer()
    text_1, text_2 = normalizer.canonical(value_1).text, normalizer.canonical(value_2).text
    if text_1 == text_2:
        return 100
    if fuzz is None:  # pragma: no cover
        from difflib import SequenceMatcher

        return int(round(SequenceMatcher(None, text_1, text_2).ratio() * 100))
    return int(max(fuzz.token_set_ratio(text_1, text_2), fuzz.ratio(text_1, text_2)))


def run_pass(
    config: dict,
    sources: Sequence[tuple[int, dict]],
    destinations: Sequence[tuple[int, dict]],
    extra_check: Callable[[dict, dict], bool] | None = None,
) -> tuple[list[PassMatch], list[PassAmbiguity]]:
    """Match unmatched ``sources`` to unmatched ``destinations``.

    Positions are the caller's identifiers for each record; rows are dicts.
    """
    validate_pass(config)
    exact_amount = config["type"] == "amount_date"
    absolute_tolerance = 0.0 if exact_amount else float(config.get("amount_tolerance") or 0)
    percent_tolerance = 0.0 if exact_amount else float(config.get("amount_tolerance_percent") or 0)
    use_dates = bool(config.get("date_source") and config.get("date_destination"))
    window = int(config.get("date_window_days") or 0)
    use_narrative = bool(config.get("narrative_source"))
    narrative_threshold = int(config.get("narrative_threshold", 85))

    indexed: list[tuple[float, int, dict, object]] = []
    for position, row in destinations:
        amount = to_number(row.get(config["amount_destination"]))
        if amount is None:
            continue
        day = parse_date_value(row.get(config["date_destination"])) if use_dates else None
        if use_dates and day is None:
            continue
        indexed.append((amount, position, row, day))
    indexed.sort(key=lambda item: (item[0], item[1]))
    amounts = [item[0] for item in indexed]

    forward: dict[int, list[tuple[int, str, bool]]] = {}
    too_many: set[int] = set()
    for source_position, source_row in sources:
        amount = to_number(source_row.get(config["amount_source"]))
        if amount is None:
            continue
        day = parse_date_value(source_row.get(config["date_source"])) if use_dates else None
        if use_dates and day is None:
            continue
        tolerance = max(absolute_tolerance, abs(amount) * percent_tolerance / 100)
        # Half a cent absorbs floating-point noise in "equal" amounts.
        low, high = bisect_left(amounts, amount - tolerance - 0.005), bisect_right(amounts, amount + tolerance + 0.005)
        if high - low > _MAX_CANDIDATES:
            too_many.add(source_position)
            continue
        candidates: list[tuple[int, str, bool]] = []
        for candidate_amount, destination_position, destination_row, destination_day in indexed[low:high]:
            parts = [
                f"amount {amount:,.2f} = {candidate_amount:,.2f}"
                if abs(amount - candidate_amount) < 0.005
                else f"amounts differ by {abs(amount - candidate_amount):,.2f} (tolerance {tolerance:,.2f})"
            ]
            exact = abs(amount - candidate_amount) < 0.005
            if use_dates:
                gap = abs((destination_day - day).days)
                if gap > window:
                    continue
                parts.append("same date" if gap == 0 else f"dates {gap} day{'s' if gap != 1 else ''} apart (±{window} allowed)")
                exact = exact and gap == 0
            else:
                exact = False  # An amount alone never identifies a record.
            if use_narrative:
                score = _narrative_score(source_row.get(config["narrative_source"]), destination_row.get(config["narrative_destination"]))
                if score < narrative_threshold:
                    continue
                parts.append(f"reference {score}% similar")
                exact = exact and score == 100
            if extra_check is not None and not extra_check(source_row, destination_row):
                continue
            candidates.append((destination_position, "; ".join(parts), exact))
        if candidates:
            forward[source_position] = candidates

    claims: dict[int, int] = {}
    for candidates in forward.values():
        for destination_position, _, _ in candidates:
            claims[destination_position] = claims.get(destination_position, 0) + 1

    matches: list[PassMatch] = []
    ambiguities: list[PassAmbiguity] = [
        PassAmbiguity(position, _MAX_CANDIDATES, "too many records in the other file have a similar amount")
        for position in sorted(too_many)
    ]
    for source_position, candidates in forward.items():
        if len(candidates) == 1 and claims[candidates[0][0]] == 1:
            destination_position, detail, exact = candidates[0]
            matches.append(PassMatch(source_position, destination_position, detail, exact))
        elif len(candidates) > 1:
            ambiguities.append(PassAmbiguity(source_position, len(candidates), f"{len(candidates)} records in the other file qualify"))
        else:
            ambiguities.append(PassAmbiguity(source_position, claims[candidates[0][0]], "another record in this file qualifies for the same match"))
    return matches, ambiguities
