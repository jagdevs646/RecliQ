"""Ordered keyless matching passes.

After the primary-key pass, each configured pass looks only at records that
are still unmatched on both sides:

* ``amount_date``      exact amount and a date within ±N days;
* ``amount_tolerance`` amount within an absolute and/or percentage range,
                       optionally with a date window;
* either pass can also require a similar narrative/reference (fuzzy score on
  normalized text, with a minimum score).

A pair is matched only when it is *mutually unique*: the source record has
exactly one qualifying candidate, and that candidate qualifies for no other
unmatched source record. Anything else is reported as ambiguous and left for
a person to decide; a pass never picks between equal candidates.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Callable, Sequence

import duckdb
import pandas as pd

from app.reconciliation_engine.cache import is_blank, parse_date_value, to_number
from app.reconciliation_engine.normalization.entities import active_normalizer

try:
    from rapidfuzz import fuzz, process
except Exception:  # pragma: no cover
    fuzz = None
    process = None

PASS_TYPES = {"amount_date": "Amount + date", "amount_tolerance": "Amount within a range"}
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
    # Equal after normalization, word order included ("NEFT ACME CORP" and
    # "Acme Corporation NEFT"): the same reference.
    identity_1 = normalizer.identity(value_1)
    if text_1 == text_2 or (identity_1 and identity_1 == normalizer.identity(value_2)):
        return 100
    if fuzz is None:  # pragma: no cover
        from difflib import SequenceMatcher

        return min(int(round(SequenceMatcher(None, text_1, text_2).ratio() * 100)), 99)
    # Different texts score at most 99: token_set_ratio gives 100 when one
    # text contains the other, and 100 means the same reference.
    return min(int(max(fuzz.token_set_ratio(text_1, text_2), fuzz.ratio(text_1, text_2))), 99)


def _is_blank_narrative(value: object) -> bool:
    if isinstance(value, str):
        text = value.strip().lower()
        return not text or text in {"nan", "none", "nat"}
    return is_blank(value)


def _narrative_scores(
    values_1: Sequence[object],
    values_2: Sequence[object],
    *,
    exact_only: bool = False,
) -> list[int]:
    """Score paired narratives in batches, caching deterministic forms per distinct value."""
    forms: dict[tuple[str, str], tuple[str, str]] = {}
    normalizer = None

    def form(value: object) -> tuple[str, str]:
        nonlocal normalizer
        key = (type(value).__name__, repr(value))
        if key not in forms:
            if normalizer is None:
                normalizer = active_normalizer()
            canonical = normalizer.canonical(value).text
            forms[key] = (canonical, normalizer.identity(value))
        return forms[key]

    scores = [0] * len(values_1)
    pending_indices: list[int] = []
    left_text: list[str] = []
    right_text: list[str] = []
    for index, (left, right) in enumerate(zip(values_1, values_2)):
        blank_1, blank_2 = _is_blank_narrative(left), _is_blank_narrative(right)
        if blank_1 or blank_2:
            continue
        try:
            identical = type(left) is type(right) and bool(left == right)
        except (TypeError, ValueError):
            identical = False
        if identical:
            scores[index] = 100
            continue
        text_1, identity_1 = form(left)
        text_2, identity_2 = form(right)
        if text_1 == text_2 or (identity_1 and identity_1 == identity_2):
            scores[index] = 100
            continue
        if exact_only:
            continue
        pending_indices.append(index)
        left_text.append(text_1)
        right_text.append(text_2)

    if not pending_indices:
        return scores
    if fuzz is None or process is None:  # pragma: no cover
        for index in pending_indices:
            scores[index] = _narrative_score(values_1[index], values_2[index])
        return scores

    token_scores = process.cpdist(left_text, right_text, scorer=fuzz.token_set_ratio, workers=-1)
    ratio_scores = process.cpdist(left_text, right_text, scorer=fuzz.ratio, workers=-1)
    for index, token_score, ratio_score in zip(pending_indices, token_scores, ratio_scores):
        scores[index] = min(int(max(token_score, ratio_score)), 99)
    return scores


def run_column_pass(
    config: dict,
    sources: Sequence[tuple[int, dict]],
    destinations: Sequence[tuple[int, dict]],
    extra_check: Callable[[dict, dict], bool] | None = None,
) -> tuple[list[PassMatch], list[PassAmbiguity]]:
    """Generate bounded amount/date candidates with DuckDB and score text in batches."""
    validate_pass(config)
    if not sources or not destinations:
        return [], []

    exact_amount = config["type"] == "amount_date"
    absolute_tolerance = 0.0 if exact_amount else float(config.get("amount_tolerance") or 0)
    percent_tolerance = 0.0 if exact_amount else float(config.get("amount_tolerance_percent") or 0)
    use_dates = bool(config.get("date_source") and config.get("date_destination"))
    window = int(config.get("date_window_days") or 0)
    use_narrative = bool(config.get("narrative_source"))
    narrative_threshold = int(config.get("narrative_threshold", 85))

    source_rows: list[dict] = []
    source_by_position: dict[int, dict] = {}
    for position, row in sources:
        amount = to_number(row.get(config["amount_source"]))
        if amount is None:
            continue
        day = parse_date_value(row.get(config["date_source"])) if use_dates else None
        if use_dates and day is None:
            continue
        source_rows.append({
            "source_position": int(position),
            "amount": amount,
            "tolerance": max(absolute_tolerance, abs(amount) * percent_tolerance / 100),
            "day": day.toordinal() if day is not None else None,
        })
        source_by_position[int(position)] = row

    destination_rows: list[dict] = []
    destination_by_position: dict[int, dict] = {}
    destination_id_by_position: dict[int, object] = {}
    for local_position, (destination_id, row) in enumerate(destinations):
        amount = to_number(row.get(config["amount_destination"]))
        if amount is None:
            continue
        day = parse_date_value(row.get(config["date_destination"])) if use_dates else None
        destination_rows.append({
            "destination_position": local_position,
            "amount": amount,
            "day": day.toordinal() if day is not None else None,
        })
        destination_by_position[local_position] = row
        destination_id_by_position[local_position] = destination_id

    if not source_rows or not destination_rows:
        return [], []

    connection = duckdb.connect(database=":memory:")
    connection.execute("PRAGMA threads=4")
    try:
        connection.register("source_rows", pd.DataFrame(source_rows))
        connection.register("destination_rows", pd.DataFrame(destination_rows))
        counts = connection.execute(
            """
            SELECT s.source_position, count(*) AS candidate_count
            FROM source_rows AS s
            JOIN destination_rows AS d
              ON d.amount BETWEEN s.amount - s.tolerance - 0.005 AND s.amount + s.tolerance + 0.005
            GROUP BY s.source_position
            """
        ).fetchall()
        count_by_source = {int(position): int(count) for position, count in counts}
        too_many = {
            position: count for position, count in count_by_source.items()
            if count > _MAX_CANDIDATES
        }
        connection.register(
            "candidate_counts",
            pd.DataFrame(
                [(position, count) for position, count in count_by_source.items()],
                columns=["source_position", "candidate_count"],
            ),
        )
        date_filter = "AND d.day IS NOT NULL AND abs(s.day - d.day) <= ?" if use_dates else ""
        query = f"""
            SELECT s.source_position, d.destination_position, s.amount, d.amount, s.tolerance,
                   s.day, d.day
            FROM source_rows AS s
            JOIN destination_rows AS d
              ON d.amount BETWEEN s.amount - s.tolerance - 0.005 AND s.amount + s.tolerance + 0.005
            LEFT JOIN candidate_counts AS c ON c.source_position = s.source_position
            WHERE coalesce(c.candidate_count, 0) <= ? {date_filter}
            ORDER BY s.source_position, d.destination_position
        """
        parameters = [_MAX_CANDIDATES, window] if use_dates else [_MAX_CANDIDATES]
        cursor = connection.execute(query, parameters)
        qualified_counts: Counter[int] = Counter()
        destination_claims: Counter[int] = Counter()
        sole_candidate: dict[int, tuple[int, str, bool]] = {}

        while batch := cursor.fetchmany(16384):
            if use_narrative:
                narrative_scores = _narrative_scores(
                    [source_by_position[int(item[0])].get(config["narrative_source"]) for item in batch],
                    [destination_by_position[int(item[1])].get(config["narrative_destination"]) for item in batch],
                    exact_only=narrative_threshold == 100,
                )
            else:
                narrative_scores = [100] * len(batch)

            for item, score in zip(batch, narrative_scores):
                source_position, local_destination = int(item[0]), int(item[1])
                amount, destination_amount, tolerance = float(item[2]), float(item[3]), float(item[4])
                source_row = source_by_position[source_position]
                destination_row = destination_by_position[local_destination]
                if use_dates and item[5] is not None and item[6] is not None:
                    gap = abs(int(item[6]) - int(item[5]))
                    if gap > window:
                        continue
                else:
                    gap = 0
                if use_narrative and score < narrative_threshold:
                    continue
                if extra_check is not None and not extra_check(source_row, destination_row):
                    continue

                parts = [
                    f"amount {amount:,.2f} = {destination_amount:,.2f}"
                    if abs(amount - destination_amount) < 0.005
                    else f"amounts differ by {abs(amount - destination_amount):,.2f} (tolerance {tolerance:,.2f})"
                ]
                exact = abs(amount - destination_amount) < 0.005
                if use_dates:
                    parts.append("same date" if gap == 0 else f"dates {gap} day{'s' if gap != 1 else ''} apart (±{window} allowed)")
                    exact = exact and gap == 0
                else:
                    exact = False
                if use_narrative:
                    parts.append(f"reference {score}% similar")
                    exact = exact and score == 100

                qualified_counts[source_position] += 1
                destination_claims[local_destination] += 1
                if qualified_counts[source_position] == 1:
                    sole_candidate[source_position] = (local_destination, "; ".join(parts), exact)

        matches: list[PassMatch] = []
        ambiguities = [
            PassAmbiguity(position, _MAX_CANDIDATES, "too many records in the other file have a similar amount")
            for position in sorted(too_many)
        ]
        for source_position, count in qualified_counts.items():
            if count > 1:
                ambiguities.append(PassAmbiguity(source_position, count, f"{count} records in the other file qualify"))
                continue
            local_destination, detail, exact = sole_candidate[source_position]
            claim_count = destination_claims[local_destination]
            if claim_count == 1:
                matches.append(PassMatch(source_position, destination_id_by_position[local_destination], detail, exact))
            else:
                ambiguities.append(PassAmbiguity(source_position, claim_count, "another record in this file qualifies for the same match"))

        return matches, sorted(ambiguities, key=lambda item: item.source_position)
    finally:
        connection.close()


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
                if destination_day is None:
                    continue
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
