"""Auto-resolution rules for recurring exceptions.

A rule explains an exception a team sees every period and clears the same way,
for example "only in the bank statement, narrative contains 'bank charges',
amount up to 500 → Bank charges, GL 6100". Rules are written by people, run
after matching in priority order (the first rule that fits wins) and label
the exception with the rule, its version, the resolution, a reason code and a
GL account. They never change which records match, with one explicit
exception: a "match to confirm" rule may confirm a proposed match.

Safety limits, enforced when a rule is saved:

* every rule needs at least one condition, and a condition on a column that
  a record does not have is false, so missing data never resolves anything;
* a field-difference rule must limit the size of the difference;
* a match-to-confirm rule must require earlier reviewer confirmation or a
  minimum confidence;
* ambiguous matches (several candidates) are never resolved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.reconciliation_engine.cache import is_blank, normalize_header, to_number

CATEGORIES = {
    "only_in_source": "Only in the source file",
    "only_in_destination": "Only in the destination file",
    "field_difference": "Difference in a compared field",
    "to_confirm": "Match to confirm",
}

TEXT_OPERATORS = {
    "contains": "contains",
    "not_contains": "does not contain",
    "equals": "is",
    "starts_with": "starts with",
    "ends_with": "ends with",
    "is_blank": "is blank",
    "not_blank": "is not blank",
}
NUMBER_OPERATORS = {
    "abs_lte": "is at most (ignoring sign)",
    "abs_gte": "is at least (ignoring sign)",
    "lte": "is at most",
    "gte": "is at least",
    "between": "is between",
}
DIFFERENCE_OPERATORS = {
    "field_is": "field is",
    "difference_abs_lte": "difference is at most",
    "difference_pct_lte": "difference is at most (%)",
}
CONFIRM_OPERATORS = {
    "previously_confirmed": "was confirmed by a reviewer at least (times)",
    "confidence_gte": "match confidence is at least (%)",
    "method_is": "was found by",
}
_ANY_COLUMN_OPERATORS = {"contains", "equals", "starts_with", "ends_with"}
_NO_VALUE_OPERATORS = {"is_blank", "not_blank"}
ALLOWED_OPERATORS = {
    "only_in_source": {**TEXT_OPERATORS, **NUMBER_OPERATORS},
    "only_in_destination": {**TEXT_OPERATORS, **NUMBER_OPERATORS},
    "field_difference": {**DIFFERENCE_OPERATORS, **TEXT_OPERATORS, **NUMBER_OPERATORS},
    "to_confirm": {**CONFIRM_OPERATORS, **TEXT_OPERATORS, **NUMBER_OPERATORS},
}
MAX_CONDITIONS = 10


@dataclass(frozen=True)
class Condition:
    operator: str
    column: str = ""
    value: object = None
    value_2: object = None


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    version: int
    category: str
    conditions: tuple[Condition, ...]
    resolution: str
    reason_code: str = ""
    gl_account: str = ""
    note: str = ""

    def label(self) -> str:
        return f"{self.name} (v{self.version})"


def _number(value: object, what: str) -> float:
    number = to_number(value)
    if number is None:
        raise ValueError(f"{what} must be a number.")
    return number


def compile_rule(data: dict) -> Rule:
    """Validate a stored/submitted rule. Raises ValueError in plain words."""
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("Give the rule a name.")
    category = data.get("category")
    if category not in CATEGORIES:
        raise ValueError(f"Unknown exception type: {category!r}.")
    raw_conditions = data.get("conditions") or []
    if not raw_conditions:
        raise ValueError("A rule needs at least one condition.")
    if len(raw_conditions) > MAX_CONDITIONS:
        raise ValueError(f"A rule can have at most {MAX_CONDITIONS} conditions.")
    allowed = ALLOWED_OPERATORS[category]
    conditions = []
    for raw in raw_conditions:
        operator = raw.get("operator")
        if operator not in allowed:
            raise ValueError(f"'{operator}' cannot be used for '{CATEGORIES[category]}'.")
        column = str(raw.get("column") or "").strip()
        value, value_2 = raw.get("value"), raw.get("value_2")
        if operator in TEXT_OPERATORS or operator in NUMBER_OPERATORS:
            if not column:
                raise ValueError(f"Choose a column for the condition '{allowed[operator]}'.")
            if column == "*" and operator not in _ANY_COLUMN_OPERATORS:
                raise ValueError(f"'Any column' only works with: {', '.join(TEXT_OPERATORS[o] for o in sorted(_ANY_COLUMN_OPERATORS))}.")
        if operator in TEXT_OPERATORS and operator not in _NO_VALUE_OPERATORS:
            value = str(value if value is not None else "").strip()
            if not value:
                raise ValueError(f"Enter the text for '{column} {TEXT_OPERATORS[operator]}'.")
        if operator in NUMBER_OPERATORS or operator in {"difference_abs_lte", "difference_pct_lte", "confidence_gte"}:
            value = _number(value, f"The value for '{allowed[operator]}'")
            if operator == "between":
                value_2 = _number(value_2, "The upper value for 'is between'")
                if value_2 < value:
                    raise ValueError("For 'is between', the second value must not be smaller than the first.")
            if operator in {"difference_abs_lte", "difference_pct_lte"} and value < 0:
                raise ValueError("A difference limit cannot be negative.")
            if operator == "difference_pct_lte" and value > 100:
                raise ValueError("A percentage difference limit cannot exceed 100%.")
            if operator == "confidence_gte" and not 50 <= value <= 100:
                raise ValueError("A minimum match confidence must be between 50% and 100%.")
        if operator == "previously_confirmed":
            value = int(_number(value if value not in (None, "") else 1, "The number of earlier confirmations"))
            if value < 1:
                raise ValueError("Require at least one earlier confirmation.")
        if operator in {"field_is", "method_is"}:
            value = str(value or "").strip()
            if not value:
                raise ValueError(f"Enter a value for '{allowed[operator]}'.")
        conditions.append(Condition(operator, column, value, value_2))

    operators = {condition.operator for condition in conditions}
    if category == "field_difference" and not operators & {"difference_abs_lte", "difference_pct_lte"}:
        raise ValueError("A field-difference rule must limit how large the difference may be.")
    if category == "to_confirm" and not operators & {"previously_confirmed", "confidence_gte"}:
        raise ValueError("A match-to-confirm rule must require earlier reviewer confirmation or a minimum confidence.")

    action = data.get("action") or {}
    resolution = str(action.get("resolution") or "").strip()
    if not resolution:
        raise ValueError("Say how matching exceptions are resolved (for example 'Bank charges').")
    for key, limit in (("resolution", 120), ("reason_code", 40), ("gl_account", 40), ("note", 500)):
        if len(str(action.get(key) or "")) > limit:
            raise ValueError(f"'{key.replace('_', ' ')}' can be at most {limit} characters.")
    return Rule(
        id=str(data.get("id") or ""),
        name=name[:200],
        version=int(data.get("version") or 1),
        category=category,
        conditions=tuple(conditions),
        resolution=resolution,
        reason_code=str(action.get("reason_code") or "").strip(),
        gl_account=str(action.get("gl_account") or "").strip(),
        note=str(action.get("note") or "").strip(),
    )


def describe_rule(rule: Rule) -> str:
    """The rule in one sentence, as shown in the app and the report."""
    parts = []
    for condition in rule.conditions:
        words = ALLOWED_OPERATORS[rule.category][condition.operator]
        column = "any column" if condition.column == "*" else condition.column
        if condition.operator in _NO_VALUE_OPERATORS:
            parts.append(f"{column} {words}")
        elif condition.operator == "between":
            parts.append(f"{column} {words} {condition.value:g} and {condition.value_2:g}")
        elif condition.operator in TEXT_OPERATORS:
            parts.append(f"{column} {words} '{condition.value}'")
        elif condition.operator in NUMBER_OPERATORS:
            parts.append(f"{column} {words} {condition.value:g}")
        elif condition.operator == "difference_pct_lte":
            parts.append(f"the difference is at most {condition.value:g}%")
        elif condition.operator == "difference_abs_lte":
            parts.append(f"the difference is at most {condition.value:g}")
        elif condition.operator == "field_is":
            parts.append(f"the field is {condition.value}")
        elif condition.operator == "previously_confirmed":
            parts.append(f"a reviewer confirmed the same pair at least {condition.value} time{'s' if condition.value != 1 else ''}")
        elif condition.operator == "confidence_gte":
            parts.append(f"match confidence is at least {condition.value:g}%")
        elif condition.operator == "method_is":
            parts.append(f"it was found by '{condition.value}'")
    target = CATEGORIES[rule.category].lower()
    return f"{target[0].upper()}{target[1:]} where {' and '.join(parts)} → {rule.resolution}."


# ── Evaluation ────────────────────────────────────────────────────────────
def _fold(value: object) -> str:
    return " ".join(str(value).split()).casefold()


class _Row:
    """Case/spacing-insensitive column lookup over one record."""

    def __init__(self, row: dict | None):
        self.values: dict[str, object] = {}
        for key, value in (row or {}).items():
            name = normalize_header(key)
            if name and not name.startswith(("NORM_", "__")) and name not in self.values:
                self.values[name] = value

    def get(self, column: str) -> tuple[bool, object]:
        name = normalize_header(column)
        return (name in self.values, self.values.get(name))


def _text_holds(operator: str, cell: object, needle: str) -> bool:
    if operator == "is_blank":
        return is_blank(cell)
    if operator == "not_blank":
        return not is_blank(cell)
    text = "" if is_blank(cell) else _fold(cell)
    needle = _fold(needle)
    return {
        "contains": needle in text,
        "not_contains": needle not in text,
        "equals": text == needle,
        "starts_with": text.startswith(needle),
        "ends_with": text.endswith(needle),
    }[operator]


def _number_holds(operator: str, cell: object, value: float, value_2: float | None) -> bool:
    number = to_number(cell)
    if number is None:
        return False
    return {
        "abs_lte": abs(number) <= value + 1e-9,
        "abs_gte": abs(number) >= value - 1e-9,
        "lte": number <= value + 1e-9,
        "gte": number >= value - 1e-9,
        "between": value - 1e-9 <= number <= (value_2 if value_2 is not None else value) + 1e-9,
    }[operator]


def _record_condition(condition: Condition, row: _Row) -> bool | None:
    """Column conditions; None when the condition is not a column condition."""
    if condition.operator in TEXT_OPERATORS:
        if condition.column == "*":
            return any(
                not is_blank(cell) and _text_holds(condition.operator, cell, str(condition.value))
                for cell in row.values.values()
            )
        present, cell = row.get(condition.column)
        return present and _text_holds(condition.operator, cell, str(condition.value or ""))
    if condition.operator in NUMBER_OPERATORS:
        present, cell = row.get(condition.column)
        return present and _number_holds(condition.operator, cell, float(condition.value), condition.value_2)
    return None


@dataclass
class Resolution:
    rule: Rule
    category: str


@dataclass
class Evaluator:
    rules: list[Rule]
    learning: object | None = None  # LearningContext
    counts: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_config(cls, rules: list[dict] | None, learning=None) -> "Evaluator | None":
        compiled = [compile_rule(rule) for rule in rules or [] if rule.get("enabled", True)]
        return cls(compiled, learning) if compiled else None

    def _first(self, category: str, check) -> Rule | None:
        for rule in self.rules:
            if rule.category == category and all(check(condition) for condition in rule.conditions):
                self.counts[rule.label()] = self.counts.get(rule.label(), 0) + 1
                return rule
        return None

    def unmatched(self, row: dict, side: str) -> Rule | None:
        record = _Row(row)
        category = "only_in_source" if side == "source" else "only_in_destination"
        return self._first(category, lambda condition: bool(_record_condition(condition, record)))

    def difference(self, field_label: str, value_1: object, value_2: object, difference: object, source_row: dict) -> Rule | None:
        record = _Row(source_row)
        diff = to_number(difference)
        base = to_number(value_2)

        def check(condition: Condition) -> bool:
            if condition.operator == "field_is":
                return normalize_header(field_label) == normalize_header(condition.value)
            if condition.operator == "difference_abs_lte":
                return diff is not None and abs(diff) <= float(condition.value) + 1e-9
            if condition.operator == "difference_pct_lte":
                return diff is not None and bool(base) and abs(diff) / abs(base) * 100 <= float(condition.value) + 1e-9
            return bool(_record_condition(condition, record))

        return self._first("field_difference", check)

    def confirmation(self, source_row: dict, key_1: object, key_2: object, confidence: int, method: str) -> Rule | None:
        record = _Row(source_row)
        history = self.learning.history(key_1, key_2) if self.learning is not None else None

        def check(condition: Condition) -> bool:
            if condition.operator == "previously_confirmed":
                return bool(history and not history.rejected and history.accepted >= int(condition.value))
            if condition.operator == "confidence_gte":
                return confidence >= float(condition.value)
            if condition.operator == "method_is":
                return _fold(condition.value) in _fold(method)
            return bool(_record_condition(condition, record))

        return self._first("to_confirm", check)


def referenced_values(rule: Rule, row: dict) -> dict:
    """The values of the columns a rule looked at, so the report shows why."""
    record = _Row(row)
    values = {}
    for condition in rule.conditions:
        if condition.column and condition.column != "*":
            present, value = record.get(condition.column)
            if present:
                values[normalize_header(condition.column)] = value
    return values


def resolution_fields(rule: Rule, category_label: str, kind: str) -> dict:
    """The columns every auto-resolved row carries in the report. ``__KIND__``
    (hidden) is the exception type, used to keep the report's counts balanced."""
    return {
        "__KIND__": kind,
        "Exception": category_label,
        "Resolution": rule.resolution,
        "Rule": rule.name,
        "Rule Version": rule.version,
        "Reason Code": rule.reason_code,
        "GL Account": rule.gl_account,
        "Note": rule.note,
    }


# ── Recurring exception signatures ────────────────────────────────────────
_WORD = re.compile(r"[a-z][a-z&']{1,}")


def text_signature(value: object) -> str | None:
    """A reusable description of a text value: its words without numbers,
    e.g. 'BANK CHARGES JAN-2026 #4411' -> 'bank charges jan'. None for
    identifiers and codes (mostly digits, or no word of 4+ letters)."""
    if is_blank(value) or to_number(value) is not None:
        return None
    text = str(value).casefold()
    alnum = [char for char in text if char.isalnum()]
    letters = [char for char in alnum if char.isalpha()]
    if not alnum or len(letters) / len(alnum) < 0.6:
        return None
    words = _WORD.findall(text)
    if not any(len(word) >= 4 for word in words):
        return None
    signature = " ".join(words[:6])
    return signature if 4 <= len(signature) <= 120 else None
