"""Deferred, human-readable explanations for reconciliation records.

The engine stores compact match facts for every row.  This module turns those
facts into the user-facing explanation only when a report or result page needs
one.  That avoids allocating a long, repeated sentence for every exact match.
"""
from __future__ import annotations

from typing import Any, Mapping


EXPLANATION_CONTEXT_KEY = "__MATCH_EXPLANATION_CONTEXT__"
EXPLANATION_METADATA_KEY = "__MATCH_EXPLANATION_METADATA__"


def explanation_metadata(*, secondary_names: str | None) -> dict[str, str]:
    """Return once-per-run facts needed to explain generic-engine records."""
    return {"secondary_names": secondary_names or ""}


def has_match_explanation(record: Mapping[str, Any]) -> bool:
    """Whether a record can expose a meaningful match explanation."""
    return bool(record.get("MATCH EXPLANATION") or record.get("IDENTITY CLASSIFICATION"))


def match_explanation(
    record: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> str | None:
    """Build a report explanation from a legacy string or compact facts.

    Stored reports made before deferred explanations retain ``MATCH
    EXPLANATION`` and therefore take the first branch unchanged.
    """
    legacy = record.get("MATCH EXPLANATION")
    if legacy:
        return str(legacy)

    raw_context = record.get(EXPLANATION_CONTEXT_KEY)
    context = raw_context if isinstance(raw_context, Mapping) else {}
    classification = str(context.get("classification") or record.get("IDENTITY CLASSIFICATION") or "")
    if not classification:
        return None

    metadata_context: Mapping[str, Any] = {}
    if isinstance(metadata, Mapping):
        candidate = metadata.get(EXPLANATION_METADATA_KEY)
        if isinstance(candidate, Mapping):
            metadata_context = candidate
    secondary_names = str(metadata_context.get("secondary_names") or "") or None
    learned_note = str(context.get("learned_note") or "")
    explicit = str(context.get("explicit") or "")
    secondary_mismatch = context.get("secondary_mismatch")

    if explicit:
        explanation = explicit
    elif context.get("unmatched_note_only") and learned_note:
        explanation = learned_note
    elif secondary_mismatch:
        if isinstance(secondary_mismatch, (list, tuple)):
            mismatch_text = "; ".join(str(item) for item in secondary_mismatch)
        else:
            mismatch_text = str(secondary_mismatch)
        explanation = f"The key exists in the other file, but {mismatch_text}."
    elif classification == "EXACT_MATCH":
        base = "Key and secondary keys matched" if secondary_names else "Key matched"
        normalization = str(record.get("NORMALIZATION APPLIED") or "")
        explanation = f"{base} after normalization ({normalization})." if normalization else f"{base}."
    elif classification == "AMBIGUOUS_MATCH":
        if secondary_names is None:
            explanation = "Several records in the other file have this key, so none was chosen."
        else:
            explanation = (
                "Several records in the other file have a similar key and the same "
                f"{secondary_names}, so none was chosen."
            )
    elif classification == "NOT_FOUND" and not context.get("has_key_result"):
        if secondary_names is None:
            explanation = "No record in the other file has this key."
        else:
            explanation = (
                "No record in the other file has this key (or a close variant) with the same "
                f"{secondary_names}."
            )
    else:
        confidence = str(record.get("MATCH CONFIDENCE") or "0%").removesuffix("%")
        threshold = str(record.get("MATCH THRESHOLD") or "None%").removesuffix("%")
        status = str(context.get("match_status") or record.get("MATCH STATUS") or "match")
        closeness = f"{confidence}% similar, {threshold}% required"
        if classification == "EXCEPTION_MATCH":
            explanation = (
                f"Key differs slightly ({status.lower()}, {closeness}), but every secondary key matched. "
                "Please confirm."
            )
        else:
            explanation = (
                "The closest record with the same "
                f"{secondary_names or 'secondary keys'} has a different key ({closeness})."
            )

    if learned_note and not context.get("unmatched_note_only"):
        explanation = f"{explanation} ({learned_note})"
    if context.get("append_learning"):
        review_history = str(record.get("REVIEW HISTORY") or "")
        learned_confidence = str(record.get("LEARNED CONFIDENCE") or "")
        if review_history:
            explanation = f"{explanation} {review_history}."
        if learned_confidence:
            explanation = f"{explanation} Learned confidence for this kind of match: {learned_confidence}."
    if context.get("auto_confirmed_by"):
        explanation = f"{explanation} Confirmed automatically by rule '{context['auto_confirmed_by']}'."
    return explanation


def materialize_match_explanation(
    record: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a shallow copy with its display explanation, if it has one."""
    explanation = match_explanation(record, metadata)
    if not explanation or record.get("MATCH EXPLANATION") == explanation:
        return dict(record)
    materialized = dict(record)
    materialized["MATCH EXPLANATION"] = explanation
    return materialized
