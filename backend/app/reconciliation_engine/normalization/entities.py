"""Deterministic normalization of business names and narrative text.

"ABC Pvt Ltd", "A.B.C. PRIVATE LIMITED" and "M/s ABC Private Ltd." all reduce
to the same comparable form, so they match through the normal (exact) matching
rules instead of appearing as "not found". Every rule that changes a value is
named, so the report can say which normalization produced a match.

Order of application (all deterministic, applied identically to both files):
1. Unicode cleanup, lower case, "&" -> "and", accounting phrases (A/c, O/s).
2. Punctuation and whitespace collapse.
3. Honorific prefixes and articles ("M/s", "Messrs", "The") removed.
4. Legal forms (Pvt, Ltd, Co, Corp, Inc, LLP ...) expanded.
5. Common business/accounting abbreviations expanded.
6. Organization synonyms (configured per rule).
7. Single-letter initials joined ("a b c" -> "abc").
8. Word order ignored (optional): tokens are sorted for the identity form.
9. Organization aliases (approved whole-value equivalences) applied last.

Fuzzy matching is never part of this layer; it runs afterwards, only where
the matcher allows it, with confidence thresholds.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from app.reconciliation_engine.cache import is_blank, normalize_text, unicode_clean

LEGAL_FORMS: dict[str, tuple[str, str]] = {
    "pvt": ("private", "Pvt → Private"),
    "pte": ("private", "Pte → Private"),
    "priv": ("private", "Priv → Private"),
    "ltd": ("limited", "Ltd → Limited"),
    "co": ("company", "Co → Company"),
    "cos": ("companies", "Cos → Companies"),
    "corp": ("corporation", "Corp → Corporation"),
    "inc": ("incorporated", "Inc → Incorporated"),
    "incorp": ("incorporated", "Incorp → Incorporated"),
    "llp": ("limited liability partnership", "LLP → Limited Liability Partnership"),
    "plc": ("public limited company", "PLC → Public Limited Company"),
    "llc": ("limited liability company", "LLC → Limited Liability Company"),
}

# Unambiguous abbreviations only. Tokens with several common meanings
# ("int": interest/international, "ind": India/industries, "exp",
# "ent", "tech") are deliberately absent; add them as organization synonyms.
ABBREVIATIONS: dict[str, tuple[str, str]] = {
    "intl": ("international", "Intl → International"),
    "bros": ("brothers", "Bros → Brothers"),
    "mfg": ("manufacturing", "Mfg → Manufacturing"),
    "mfrs": ("manufacturers", "Mfrs → Manufacturers"),
    "svc": ("services", "Svc → Services"),
    "svcs": ("services", "Svcs → Services"),
    "assoc": ("associates", "Assoc → Associates"),
    "govt": ("government", "Govt → Government"),
    "dept": ("department", "Dept → Department"),
    "natl": ("national", "Natl → National"),
    "hldgs": ("holdings", "Hldgs → Holdings"),
    "grp": ("group", "Grp → Group"),
    "mgmt": ("management", "Mgmt → Management"),
    "engg": ("engineering", "Engg → Engineering"),
    "acct": ("account", "Acct → Account"),
    "bal": ("balance", "Bal → Balance"),
    "chq": ("cheque", "Chq → Cheque"),
    "pmt": ("payment", "Pmt → Payment"),
    "pymt": ("payment", "Pymt → Payment"),
    "recd": ("received", "Recd → Received"),
    "rcvd": ("received", "Rcvd → Received"),
    "txn": ("transaction", "Txn → Transaction"),
    "adv": ("advance", "Adv → Advance"),
    "amt": ("amount", "Amt → Amount"),
    "qty": ("quantity", "Qty → Quantity"),
    "cr": ("credit", "Cr → Credit"),
    "dr": ("debit", "Dr → Debit"),
    "coop": ("cooperative", "Coop → Cooperative"),
}

IGNORED_WORDS: dict[str, str] = {"the": "Ignored 'The'", "messrs": "Ignored 'Messrs'"}

# (pattern, replacement, rule label, category controlling whether it applies)
_PHRASES: tuple[tuple[re.Pattern[str], str, str, str], ...] = (
    (re.compile(r"^\s*m\s*/\s*s\b\.?"), " ", "Ignored 'M/s'", "prefix"),
    (re.compile(r"\(\s*p\s*\)"), " private ", "(P) → Private", "legal"),
    (re.compile(r"\ba\s*/\s*c\b"), " account ", "A/c → Account", "abbreviation"),
    (re.compile(r"\bo\s*/\s*s\b"), " outstanding ", "O/s → Outstanding", "abbreviation"),
    (re.compile(r"&"), " and ", "& → And", "always"),
)


@dataclass(frozen=True)
class CanonicalForm:
    text: str
    sorted_key: str
    rules: tuple[str, ...]


@dataclass(frozen=True)
class NormalizerConfig:
    legal_forms: bool = True
    abbreviations: bool = True
    ignore_prefixes: bool = True
    join_initials: bool = True
    word_order: bool = True
    # token -> replacement, from organization settings.
    synonyms: tuple[tuple[str, str], ...] = ()
    # (variant, canonical) whole-value equivalences approved by a person.
    aliases: tuple[tuple[str, str], ...] = ()


class EntityNormalizer:
    def __init__(self, config: NormalizerConfig | None = None):
        self.config = config or NormalizerConfig()
        self._cache: dict[str, CanonicalForm] = {}
        self._synonyms = {
            normalize_text(term): (normalize_text(replacement), f"Synonym: {term} → {replacement}")
            for term, replacement in self.config.synonyms
            if normalize_text(term) and normalize_text(replacement)
        }
        # Aliases are keyed on the alias-free identity of each side.
        self._aliases: dict[str, tuple[str, str]] = {}
        for variant, canonical in self.config.aliases:
            variant_key, canonical_key = self._identity_without_aliases(variant), self._identity_without_aliases(canonical)
            if variant_key and canonical_key and variant_key != canonical_key:
                self._aliases[variant_key] = (canonical_key, f"Alias: {variant} = {canonical}")

    # -- canonical forms --------------------------------------------------
    def canonical(self, value: object) -> CanonicalForm:
        if is_blank(value):
            return CanonicalForm("", "", ())
        raw = str(value)
        cached = self._cache.get(raw)
        if cached is not None:
            return cached
        form = self._build(raw)
        if len(self._cache) < 200_000:
            self._cache[raw] = form
        return form

    def _build(self, raw: str) -> CanonicalForm:
        rules: list[str] = []
        text = unicode_clean(raw).lower()
        enabled = {
            "always": True,
            "prefix": self.config.ignore_prefixes,
            "legal": self.config.legal_forms,
            "abbreviation": self.config.abbreviations,
        }
        for pattern, replacement, label, category in _PHRASES:
            if not enabled[category]:
                continue
            updated = pattern.sub(replacement, text)
            if updated != text:
                rules.append(label)
                text = updated
        tokens = normalize_text(text).split()

        output: list[str] = []
        for token in tokens:
            if self.config.ignore_prefixes and token in IGNORED_WORDS and len(tokens) > 1:
                rules.append(IGNORED_WORDS[token])
                continue
            replacement = None
            if token in self._synonyms:
                replacement = self._synonyms[token]
            elif self.config.legal_forms and token in LEGAL_FORMS:
                replacement = LEGAL_FORMS[token]
            elif self.config.abbreviations and token in ABBREVIATIONS:
                replacement = ABBREVIATIONS[token]
            if replacement is not None:
                output.extend(replacement[0].split())
                rules.append(replacement[1])
            else:
                output.append(token)

        if self.config.join_initials:
            output, joined = _join_initials(output)
            if joined:
                rules.append("Initials joined (A.B.C. → ABC)")

        text = " ".join(output)
        return CanonicalForm(text, " ".join(sorted(output)), tuple(dict.fromkeys(rules)))

    def _identity_without_aliases(self, value: object) -> str:
        form = self.canonical(value)
        return form.sorted_key if self.config.word_order else form.text

    def identity(self, value: object) -> str | None:
        """The exact-match form used for indexing, grouping and comparison."""
        key = self._identity_without_aliases(value)
        if not key:
            return None
        alias = self._aliases.get(key)
        return alias[0] if alias else key

    def equivalent(self, value_1: object, value_2: object) -> bool:
        identity_1, identity_2 = self.identity(value_1), self.identity(value_2)
        return identity_1 is not None and identity_1 == identity_2

    def explain(self, value_1: object, value_2: object) -> list[str]:
        """Rules that made two different-looking values equal (empty when the
        values were already equal after case/space cleanup)."""
        if normalize_text(value_1) == normalize_text(value_2):
            return []
        form_1, form_2 = self.canonical(value_1), self.canonical(value_2)
        rules = list(dict.fromkeys(form_1.rules + form_2.rules))
        if self.config.word_order and form_1.text != form_2.text and form_1.sorted_key == form_2.sorted_key:
            rules.append("Word order ignored")
        for form in (form_1, form_2):
            key = form.sorted_key if self.config.word_order else form.text
            if key in self._aliases:
                rules.append(self._aliases[key][1])
        return list(dict.fromkeys(rules))


def _join_initials(tokens: list[str]) -> tuple[list[str], bool]:
    output: list[str] = []
    run: list[str] = []
    joined = False
    for token in tokens + [""]:
        if len(token) == 1 and token.isalpha():
            run.append(token)
            continue
        if len(run) >= 2:
            output.append("".join(run))
            joined = True
        else:
            output.extend(run)
        run = []
        if token:
            output.append(token)
    return output, joined


# The normalizer for the sheet rule being executed. Matching functions read
# it, so one job's organization aliases never leak into another's.
_DEFAULT = EntityNormalizer()
_active: ContextVar[EntityNormalizer] = ContextVar("active_entity_normalizer", default=_DEFAULT)


def active_normalizer() -> EntityNormalizer:
    return _active.get()


@contextmanager
def use_normalizer(normalizer: EntityNormalizer) -> Iterator[EntityNormalizer]:
    token = _active.set(normalizer)
    try:
        yield normalizer
    finally:
        _active.reset(token)


def build_normalizer(settings: dict | None, saved_aliases: Iterable[tuple[str, str]] = ()) -> EntityNormalizer:
    """Create a normalizer from a sheet rule's settings plus approved aliases."""
    settings = settings or {}
    aliases = [
        (variant, entry.get("canonical", ""))
        for entry in settings.get("aliases", []) or []
        for variant in entry.get("variants", []) or []
    ]
    if settings.get("use_saved_aliases", True):
        aliases.extend(saved_aliases)
    return EntityNormalizer(
        NormalizerConfig(
            legal_forms=bool(settings.get("legal_forms", True)),
            abbreviations=bool(settings.get("abbreviations", True)),
            ignore_prefixes=bool(settings.get("ignore_prefixes", True)),
            join_initials=bool(settings.get("join_initials", True)),
            word_order=bool(settings.get("word_order", True)),
            synonyms=tuple(
                (entry.get("term", ""), entry.get("replacement", ""))
                for entry in settings.get("synonyms", []) or []
            ),
            aliases=tuple(aliases),
        )
    )


@dataclass
class NormalizationTrace:
    """Collects which rules contributed to matches, for reports."""

    counts: dict[str, int] = field(default_factory=dict)

    def add(self, rules: Iterable[str]) -> None:
        for rule in rules:
            self.counts[rule] = self.counts.get(rule, 0) + 1
