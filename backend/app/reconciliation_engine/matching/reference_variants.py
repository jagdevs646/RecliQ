"""Reference-number variants: pair invoice numbers that are written differently.

When the normalized primary key finds no partner, these named, deterministic
rules are tried in order on reference-like keys (invoice numbers and codes):

* ``leading_zeros`` "INV-0105" = "INV-105"              counted as matched
* ``look_alike``    "INV-1O5"  = "INV-105" (O, I, L or S between digits)  review
* ``prefix_year``   "INV/2026-27/105" = "105"           review

Every pairing records the rule that made it. A fuzzy percentage is
deliberately not used: different invoices of one series score 86-93%
(INV-1001 / INV-1002) while the same invoice written two ways scores 40-92%,
so no threshold separates them (docs/adr/0002).

The ``suggestions`` level adds review-only proposals for keys that still have
no partner; see ``ReferenceSuggester``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from app.reconciliation_engine.cache import compact_identifier, is_blank
from app.reconciliation_engine.cache.cache_manager import cached_unicode_clean

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover
    fuzz = None

# How strictly reference numbers (invoice numbers, codes) must agree.
REFERENCE_LEVELS = ("exact", "formatting", "variants", "suggestions")
DEFAULT_REFERENCE_LEVEL = "formatting"  # Saved plans without a level keep today's behaviour.
REFERENCE_LEVEL_LABELS = {
    "exact": "Exact",
    "formatting": "Formatting only",
    "variants": "Variants",
    "suggestions": "Variants + suggestions",
}

# Key types the variant rules apply to. GSTIN and PAN have fixed formats, so a
# "variant" of one is a different taxpayer, never the same one written twice.
VARIANT_KEY_TYPES = {"invoice", "identifier"}

# Document-type words that systems add or drop without changing the number.
# Credit/debit notes (CN, DN) and pro-forma invoices (PI) are different
# documents, so they are deliberately not listed.
KNOWN_PREFIXES = {
    "INV", "INVOICE", "BILL", "TAX", "TI", "SI", "GST", "DOC", "NO", "NUM", "NUMBER", "REF", "VCH", "VOUCHER",
}

_LOOK_ALIKES = {"O": "0", "o": "0", "I": "1", "i": "1", "L": "1", "l": "1", "S": "5", "s": "5"}
_LOOK_ALIKE_BETWEEN_DIGITS = re.compile(r"(?<=\d)[OoIiLlSs](?=\d)")
_LEADING_ZEROS = re.compile(r"(?<![0-9])0+(?=[0-9])")
_FY_PREFIXED = re.compile(r"\bFY\s*'?(\d{4}|\d{2})(?:\s*[-/]\s*(\d{4}|\d{2}))?(?!\d)")
_YEAR_RANGE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*[-/]\s*((?:19|20)\d{2}|\d{2})(?!\d)")
_DIGIT_GROUP = re.compile(r"\d+")
_LETTER_GROUP = re.compile(r"[A-Z]+")


def reference_level(similarity_policy: dict | None) -> str:
    level = str((similarity_policy or {}).get("reference_matching") or DEFAULT_REFERENCE_LEVEL)
    if level not in REFERENCE_LEVELS:
        raise ValueError(f"Reference matching must be one of: {', '.join(REFERENCE_LEVELS)}.")
    return level


def exact_reference_identity(value: object) -> str | None:
    """The 'Exact' level: identical apart from letter case and extra spaces."""
    if is_blank(value):
        return None
    return " ".join(cached_unicode_clean(str(value)).split()).casefold() or None


@dataclass(frozen=True)
class VariantRule:
    name: str
    label: str           # Shown in the report: "Key matched after removing leading zeros".
    explanation: str     # "differs only by leading zeros".
    needs_review: bool


VARIANT_RULES = (
    VariantRule("leading_zeros", "Key format differs (leading zeros)", "leading zeros", needs_review=False),
    VariantRule("look_alike", "Key variant (look-alike characters)", "look-alike characters (O for 0, I or L for 1, S for 5)", needs_review=True),
    VariantRule("prefix_year", "Key variant (prefix or year)", "a document prefix or financial-year part", needs_review=True),
)


@dataclass(frozen=True)
class VariantForm:
    """One key component in a rule's comparable form."""

    key: object
    changed: bool                 # The rule altered this value (so it explains a pairing).
    years: frozenset[int] = frozenset()


def _strip_leading_zeros(text: str) -> str:
    return _LEADING_ZEROS.sub("", text)


def _leading_zero_form(value: str) -> VariantForm | None:
    compact = compact_identifier(_strip_leading_zeros(value))
    if not compact or not any(char.isdigit() for char in compact):
        return None
    return VariantForm(compact, compact != compact_identifier(value))


def _look_alike_form(value: str) -> VariantForm | None:
    replaced = value
    while True:  # "1OO5" needs two rounds: each O must sit between digits.
        updated = _LOOK_ALIKE_BETWEEN_DIGITS.sub(lambda match: _LOOK_ALIKES[match.group(0)], replaced)
        if updated == replaced:
            break
        replaced = updated
    base = _leading_zero_form(replaced)
    if base is None:
        return None
    return VariantForm(base.key, replaced != value)


def _consecutive_years(first: str, second: str) -> int | None:
    """Start year of a financial year such as 2026-27 or 2026-2027, else None."""
    start = int(first) if len(first) == 4 else 2000 + int(first)
    end = int(second) if len(second) == 4 else (start // 100) * 100 + int(second)
    return start if end == start + 1 else None


def _prefix_year_form(value: str) -> VariantForm | None:
    text = cached_unicode_clean(value).upper()
    years: set[int] = set()

    def drop_fy(match: re.Match) -> str:
        first, second = match.group(1), match.group(2)
        if second is None:
            years.add(int(first) if len(first) == 4 else 2000 + int(first))
            return " "
        start = _consecutive_years(first, second)
        if start is None:
            return match.group(0)
        years.add(start)
        return " "

    def drop_year_range(match: re.Match) -> str:
        start = _consecutive_years(match.group(1), match.group(2))
        if start is None:
            return match.group(0)
        years.add(start)
        return " "

    text = _FY_PREFIXED.sub(drop_fy, text)
    text = _YEAR_RANGE.sub(drop_year_range, text)
    digit_groups = _DIGIT_GROUP.findall(text)
    # One lone four-digit year segment ("INV/2026/0105") is a year when another
    # number remains; "2026" on its own is the number itself.
    if not years and len(digit_groups) > 1:
        for position, group in enumerate(digit_groups):
            if len(group) == 4 and 1990 <= int(group) <= 2099:
                years.add(int(group))
                del digit_groups[position]
                break
    if not digit_groups:
        return None
    core = ".".join(group.lstrip("0") or "0" for group in digit_groups)
    # Letters that are not a known document word (a branch or series code
    # such as "A" or "MUM") must agree on both sides, so A-105 never pairs B-105.
    letters = tuple(word for word in _LETTER_GROUP.findall(text) if word not in KNOWN_PREFIXES)
    changed = bool(years) or any(word in KNOWN_PREFIXES for word in _LETTER_GROUP.findall(text))
    return VariantForm((letters, core), changed, frozenset(years))


_FORMS = {
    "leading_zeros": _leading_zero_form,
    "look_alike": _look_alike_form,
    "prefix_year": _prefix_year_form,
}


def component_form(rule: VariantRule, value: object) -> VariantForm | None:
    if is_blank(value):
        return None
    return _FORMS[rule.name](str(value))


def key_forms(
    rule: VariantRule,
    values: Sequence[object],
    variant_components: Sequence[bool],
    identities: Sequence[object],
) -> tuple[tuple, bool, tuple[frozenset[int], ...]] | None:
    """A whole key's comparable form under ``rule``.

    Reference components take the rule's form; every other component keeps
    its exact normalized identity (``identities``), so a composite key such as
    Invoice + GSTIN still needs the GSTIN to match exactly.
    """
    parts: list[object] = []
    years: list[frozenset[int]] = []
    changed = False
    for value, is_variant, identity in zip(values, variant_components, identities):
        if not is_variant:
            if identity is None:
                return None
            parts.append(identity)
            years.append(frozenset())
            continue
        form = component_form(rule, value)
        if form is None:
            return None
        parts.append(form.key)
        years.append(form.years)
        changed = changed or form.changed
    return tuple(parts), changed, tuple(years)


def years_compatible(left: Sequence[frozenset[int]], right: Sequence[frozenset[int]]) -> bool:
    """A financial year may be missing on one side, never different on both."""
    return all(not a or not b or a == b for a, b in zip(left, right))


# ── Review-only suggestions ───────────────────────────────────────────────
SUGGESTION_MIN_SCORE = 80
SUGGESTION_MIN_LEAD = 5
_SUGGESTION_CANDIDATES = 20
# A three-character piece shared by more references than this ("inv") says
# nothing about which one is meant; it is skipped when gathering candidates.
_MAX_POSTING = 2000


def suggestion_form(value: object) -> str:
    """Comparable text for a similarity suggestion: compact, no leading zeros."""
    if is_blank(value):
        return ""
    return compact_identifier(_strip_leading_zeros(str(value)))


def _trigrams(text: str) -> set[str]:
    padded = f"^{text}$"
    return {padded[index:index + 3] for index in range(len(padded) - 2)}


def similarity(left: str, right: str) -> int:
    if not left or not right:
        return 0
    if fuzz is None:  # pragma: no cover
        from difflib import SequenceMatcher
        return int(round(SequenceMatcher(None, left, right).ratio() * 100))
    return int(round(fuzz.ratio(left, right)))


class ReferenceSuggester:
    """Bounded candidate search for similar references.

    Candidates come from shared three-character pieces, never from scoring
    every destination row, so a 50,000 × 50,000 run stays near linear.
    """

    def __init__(self, destinations: Iterable[tuple[object, str]]):
        self.forms: dict[object, str] = {}
        self.postings: dict[str, list[object]] = {}
        for index, form in destinations:
            if not form:
                continue
            self.forms[index] = form
            for gram in _trigrams(form):
                self.postings.setdefault(gram, []).append(index)

    def candidates(self, form: str) -> list[tuple[object, int]]:
        """Destinations scoring at least ``SUGGESTION_MIN_SCORE``, best first."""
        if not form:
            return []
        shared: dict[object, int] = {}
        for gram in _trigrams(form):
            posting = self.postings.get(gram)
            if not posting or len(posting) > _MAX_POSTING:
                continue
            for index in posting:
                shared[index] = shared.get(index, 0) + 1
        nearest = sorted(shared, key=lambda index: -shared[index])[:_SUGGESTION_CANDIDATES]
        scored = [(index, similarity(form, self.forms[index])) for index in nearest]
        return sorted(
            [(index, score) for index, score in scored if score >= SUGGESTION_MIN_SCORE],
            key=lambda item: -item[1],
        )
