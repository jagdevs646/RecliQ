"""Named match levels (docs/adr/0002).

The screen offers named levels instead of bare numbers; the request still
carries the numbers, so saved plans keep working and a value that is not a
level is simply "Custom". This module names a value for reports. It must stay
in step with ``frontend/src/lib/matchLevels.ts``; the test
``test_match_levels_match_the_screen`` checks both tables.
"""
from __future__ import annotations

# Text and narratives: minimum similarity in percent.
TEXT_LEVELS = (
    ("exact", "Exact", 100),
    ("very_close", "Very close", 90),
    ("close", "Close", 80),
    ("similar", "Similar", 70),
    ("loose", "Loose", 60),
)

# Amounts: (absolute ±, percent ±). Exact is no tolerance.
AMOUNT_LEVELS = (
    ("exact", "Exact", 0.0, 0.0),
    ("rounding", "Rounding", 0.05, 0.0),
    ("one", "±1", 1.0, 0.0),
    ("half_percent", "±0.5%", 0.0, 0.5),
    ("one_percent", "±1%", 0.0, 1.0),
)

# Dates: ± days.
DATE_LEVELS = (
    ("same_day", "Same day", 0),
    ("one_day", "±1 day", 1),
    ("three_days", "±3 days", 3),
    ("seven_days", "±7 days", 7),
)


def text_level_name(similarity: float | None) -> str | None:
    if similarity is None:
        return None
    return next((label for _, label, value in TEXT_LEVELS if value == similarity), None)


def amount_level_name(amount: float | None, percent: float | None) -> str | None:
    amount, percent = float(amount or 0), float(percent or 0)
    return next(
        (label for _, label, level_amount, level_percent in AMOUNT_LEVELS
         if level_amount == amount and level_percent == percent),
        None,
    )


def date_level_name(days: int | None) -> str | None:
    days = int(days or 0)
    return next((label for _, label, value in DATE_LEVELS if value == days), None)
