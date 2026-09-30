"""Tolerance bands: accept small differences after matching.

Matching decides which records pair up; a tolerance band then decides which
of the remaining field differences are small enough to accept, for example
"Debit ±0.05" (rounding), "Amount ±1%" or "Value date ±3 days". It runs after
reconciliation and before the report is written. Text can be accepted from a
minimum similarity ("Narration at least 75% similar"), using the same score
the report shows in its Similarity column:

* a compared-field difference within a band is no longer reported as a
  difference, and the record says which difference was accepted and why;
* a record left with no differences moves to Matched (a match that still
  needs confirming stays in review: tolerance says nothing about identity);
* every other difference is left exactly as it was.

A band never changes which records pair up, so a difference beyond it is
still shown on the matched pair, never turned into two missing records.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.reconciliation_engine.cache import normalize_header, parse_date_value, to_number

ALL_FIELDS = "*"
# Differences are rounded to cents by the engine; allow for float noise.
_EPSILON = 1e-9


@dataclass(frozen=True)
class Band:
    field: str = ALL_FIELDS
    amount: float | None = None
    percent: float | None = None
    days: int | None = None
    similarity: int | None = None  # Text: at least this % similar.

    def applies_to(self, labels: set[str]) -> bool:
        return self.field == ALL_FIELDS or self.field in labels

    def describe(self) -> str:
        limits = []
        if self.amount:
            limits.append(f"±{self.amount:g}")
        if self.percent:
            limits.append(f"±{self.percent:g}%")
        if self.days:
            limits.append(f"±{self.days} day{'s' if self.days != 1 else ''}")
        if self.similarity:
            limits.append(f"text at least {self.similarity}% similar")
        target = "Every compared field" if self.field == ALL_FIELDS else self.field
        return f"{target}: {' or '.join(limits)}"


def prepare_bands(configured: list[dict] | None, compared: list[tuple[str, str]]) -> list[Band]:
    """Validate bands against the compared fields ``(source label, destination
    label)``. A band names a field by either side's column; specific fields are
    checked before "every field", so a narrower band always wins."""
    known = {label: source for source, destination in compared for label in (source, destination)}
    bands = []
    for item in configured or []:
        field = str(item.get("field") or ALL_FIELDS).strip()
        if field != ALL_FIELDS:
            normalized = normalize_header(field)
            if normalized not in known:
                raise ValueError(f"Tolerance field '{field}' is not one of the compared fields.")
            field = known[normalized]
        band = Band(
            field=field,
            amount=float(item["amount"]) if item.get("amount") else None,
            percent=float(item["percent"]) if item.get("percent") else None,
            days=int(item["days"]) if item.get("days") else None,
            similarity=int(item["similarity"]) if item.get("similarity") else None,
        )
        if not (band.amount or band.percent or band.days or band.similarity):
            raise ValueError("A tolerance needs an amount, a percentage, a number of days or a text similarity above 0.")
        bands.append(band)
    return sorted(bands, key=lambda band: band.field == ALL_FIELDS)


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _share(percent: float) -> str:
    return f"{percent:.2f}%" if percent >= 0.01 else "under 0.01%"


def accepted_reason(label: str, value_1: object, value_2: object, difference: object, bands: list[Band],
                    destination_label: str = "", confidence: object = None) -> str | None:
    """Why this field's difference is within tolerance, or None if it is not.

    Numbers use the amount or percentage limit (either is enough; percent of
    the destination value, as in the report's Difference % column); dates
    use the days limit; other text uses the minimum similarity, compared
    with ``confidence``, the engine's similarity score for the two values.
    """
    labels = {label, destination_label} - {""}
    diff = to_number(difference)
    score = to_number(str(confidence).rstrip("%")) if confidence not in (None, "") else None
    for band in bands:
        if not band.applies_to(labels):
            continue
        if diff is not None:
            size = abs(diff)
            if band.amount and size <= band.amount + _EPSILON:
                return f"{label} differs by {_money(size)}, within ±{band.amount:g}"
            base = to_number(value_2)
            if band.percent and base:
                share = size / abs(base) * 100
                if share <= band.percent + _EPSILON:
                    return f"{label} differs by {_money(size)} ({_share(share)}), within ±{band.percent:g}%"
            continue
        date_1, date_2 = parse_date_value(value_1), parse_date_value(value_2)
        if date_1 is not None and date_2 is not None:
            gap = abs((date_1 - date_2).days)
            if band.days and gap <= band.days:
                return f"{label} differs by {gap} day{'s' if gap != 1 else ''}, within ±{band.days} day{'s' if band.days != 1 else ''}"
            continue  # Dates are never accepted for looking alike.
        if band.similarity and score is not None and score >= band.similarity:
            return f"{label} is {score:g}% similar, at least {band.similarity}% required"
    return None


_FIELD_SUFFIXES = (" (FILE 1)", " (FILE 2)", " DIFF", " CONFIDENCE", " STATUS")


@dataclass
class ToleranceOutcome:
    accepted_fields: int = 0      # Field differences within tolerance.
    moved_to_matched: int = 0     # Records whose every difference was accepted.
    cleared_records: int = 0      # Records left with no field difference (moved or still to confirm).


def apply_tolerance_bands(
    results: list[dict],
    matched: list[dict],
    bands: list[Band],
    compared: list[tuple[str, str]],
    stays_in_review,
) -> ToleranceOutcome:
    """Accept differences within tolerance, in place.

    ``results`` are matched records with differences (or awaiting
    confirmation), ``matched`` the fully matched ones. ``stays_in_review``
    says whether a record must stay in ``results`` even without differences.
    """
    outcome = ToleranceOutcome()
    if not bands:
        return outcome
    kept: list[dict] = []
    for record in results:
        reasons = []
        for label, destination_label in compared:
            if f"{label} STATUS" not in record:
                continue
            reason = accepted_reason(
                label, record.get(f"{label} (FILE 1)"), record.get(f"{label} (FILE 2)"), record.get(f"{label} DIFF"),
                bands, destination_label, record.get(f"{label} CONFIDENCE"),
            )
            if reason is None:
                continue
            reasons.append(reason)
            for suffix in _FIELD_SUFFIXES:
                record.pop(f"{label}{suffix}", None)
        if not reasons:
            kept.append(record)
            continue
        outcome.accepted_fields += len(reasons)
        record["WITHIN TOLERANCE"] = "; ".join(reasons)
        if any(f"{label} STATUS" in record for label, _ in compared):
            kept.append(record)
            continue
        outcome.cleared_records += 1
        if stays_in_review(record):
            kept.append(record)
        else:
            outcome.moved_to_matched += 1
            matched.append(record)
    results[:] = kept
    return outcome
