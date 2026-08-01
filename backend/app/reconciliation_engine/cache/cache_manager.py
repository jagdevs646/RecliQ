from __future__ import annotations

import math
import re
import unicodedata
from datetime import date
from functools import lru_cache
from typing import Tuple

import pandas as pd

BUSINESS_SYNONYMS = {
    "pvt": "private",
    "pvt.": "private",
    "priv": "private",
    "ltd": "limited",
    "ltd.": "limited",
    "co": "company",
    "co.": "company",
    "corp": "corporation",
    "corp.": "corporation",
    "inc": "incorporated",
    "inc.": "incorporated",
    "llp": "limited liability partnership",
}


@lru_cache(maxsize=16384)
def cached_normalize_header(value: str) -> str:
    return value.upper().strip().replace("\n", "").replace("\r", "")


def normalize_header(value: object) -> str:
    if value is None:
        return ""
    return cached_normalize_header(str(value))


def is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    text = str(value).strip()
    return text == "" or text.lower() in {"nan", "none", "nat"}


@lru_cache(maxsize=32768)
def cached_unicode_clean(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def unicode_clean(value: object) -> str:
    if is_blank(value):
        return ""
    return cached_unicode_clean(str(value))


@lru_cache(maxsize=32768)
def cached_normalize_text(text: str, use_synonyms: bool = False) -> str:
    cleaned = cached_unicode_clean(text).lower().strip()
    cleaned = re.sub(r"[/_.-]+", " ", cleaned)
    cleaned = re.sub(r"[^\w\s&]", " ", cleaned, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if use_synonyms and cleaned:
        words: list[str] = []
        for token in cleaned.split():
            replacement = BUSINESS_SYNONYMS.get(token, token)
            words.extend(str(replacement).split())
        cleaned = " ".join(words)

    return cleaned


def normalize_text(value: object, use_synonyms: bool = False) -> str:
    if is_blank(value):
        return ""
    return cached_normalize_text(str(value), use_synonyms=use_synonyms)


@lru_cache(maxsize=32768)
def cached_compact_identifier(text: str) -> str:
    cleaned = cached_unicode_clean(text).lower()
    return re.sub(r"[^a-z0-9]", "", cleaned)


def compact_identifier(value: object) -> str:
    if is_blank(value):
        return ""
    return cached_compact_identifier(str(value))


@lru_cache(maxsize=32768)
def cached_tokens(text: str, use_synonyms: bool = False) -> Tuple[str, ...]:
    norm = cached_normalize_text(text, use_synonyms=use_synonyms)
    return tuple(token for token in norm.split() if token)


def tokens(value: object, use_synonyms: bool = False) -> list[str]:
    if is_blank(value):
        return []
    return list(cached_tokens(str(value), use_synonyms=use_synonyms))


@lru_cache(maxsize=32768)
def cached_sorted_token_key(text: str, use_synonyms: bool = False) -> str:
    toks = cached_tokens(text, use_synonyms=use_synonyms)
    return " ".join(sorted(toks))


def sorted_token_key(value: object, use_synonyms: bool = False) -> str:
    if is_blank(value):
        return ""
    return cached_sorted_token_key(str(value), use_synonyms=use_synonyms)


@lru_cache(maxsize=32768)
def cached_to_number_from_str(text: str) -> float | None:
    text = cached_unicode_clean(text)
    text = text.replace(",", "").replace(" ", "")
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    text = re.sub(r"[^0-9.+-]", "", text)
    if text in {"", ".", "+", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_number(value: object) -> float | None:
    if is_blank(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    return cached_to_number_from_str(str(value))


@lru_cache(maxsize=32768)
def cached_parse_date_str(text: str) -> date | None:
    try:
        parsed = pd.to_datetime(text, errors="coerce")
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    return parsed.date()


def parse_date_value(value: object) -> date | None:
    if is_blank(value):
        return None
    if isinstance(value, date) and not isinstance(value, datetime_type_check := type(value)):
        return value
    if hasattr(value, "date") and callable(getattr(value, "date")):
        try:
            return value.date()
        except Exception:
            pass
    return cached_parse_date_str(str(value))
