"""What reviewers' decisions teach a reconciliation run.

Reviewers confirm or reject proposed matches ("Matches to confirm"). A run
uses that history in three conservative ways:

* A rejected pairing is remembered and not proposed again. If only one other
  candidate then qualifies, that one is proposed instead (still to confirm).
* A pairing confirmed before is labelled "confirmed by a reviewer N times";
  it stays "to confirm" unless an auto-resolution rule the organization
  enabled says otherwise.
* Each matching method (e.g. "Similar key (company name)" at 90-94%, or a
  given amount + date pass) gets a learned weight: the share of its proposals
  reviewers confirmed. It is shown as a learned confidence with the evidence
  behind it, and feeds threshold suggestions; it never changes a threshold.

Nothing here turns a non-match into a match.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.reconciliation_engine.normalization.entities import EntityNormalizer

MIN_EVIDENCE = 5  # Decisions needed before a learned confidence is shown.
_PLAIN = EntityNormalizer()  # Built-in rules only: learning never feeds itself.


def value_key(value: object) -> str:
    return _PLAIN.identity("" if value is None else str(value)) or ""


def pair_key(value_1: object, value_2: object) -> str:
    return "||".join(sorted((value_key(value_1), value_key(value_2))))


def confidence_band(method: str, confidence: int | None) -> str:
    """Similar-key decisions are grouped in 5-point score bands; a keyless
    pass proposes at one fixed confidence, so it is a single band."""
    if confidence is None or not str(method).lower().startswith("similar key"):
        return "all"
    low = max(0, min(95, (int(confidence) // 5) * 5))
    return f"{low}-{low + 4}%"


def wilson_lower_bound(accepted: int, total: int, z: float = 1.96) -> float:
    """Conservative acceptance rate: the lower end of the 95% interval."""
    if total <= 0:
        return 0.0
    rate = accepted / total
    denominator = 1 + z * z / total
    centre = rate + z * z / (2 * total)
    margin = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
    return max(0.0, (centre - margin) / denominator)


@dataclass
class PairHistory:
    accepted: int = 0
    rejected: bool = False
    rejected_on: str = ""


@dataclass
class MethodWeight:
    accepted: int = 0
    rejected: int = 0

    @property
    def total(self) -> int:
        return self.accepted + self.rejected

    @property
    def rate(self) -> float:
        return self.accepted / self.total if self.total else 0.0


@dataclass
class LearningContext:
    pairs: dict[str, PairHistory] = field(default_factory=dict)
    # method label -> confidence band -> weight
    methods: dict[str, dict[str, MethodWeight]] = field(default_factory=dict)

    def history(self, value_1: object, value_2: object) -> PairHistory | None:
        key = pair_key(value_1, value_2)
        return self.pairs.get(key) if key.strip("|") else None

    def is_rejected(self, value_1: object, value_2: object) -> bool:
        history = self.history(value_1, value_2)
        return bool(history and history.rejected)

    def weight(self, method: str, confidence: int | None) -> MethodWeight | None:
        weight = self.methods.get(method, {}).get(confidence_band(method, confidence))
        return weight if weight and weight.total >= MIN_EVIDENCE else None

    def describe(self, value_1: object, value_2: object, method: str, confidence: int | None) -> tuple[str, str]:
        """(review history, learned confidence) texts for a match to confirm."""
        history = self.history(value_1, value_2)
        review = ""
        if history and history.accepted:
            review = f"Confirmed by a reviewer {history.accepted} time{'s' if history.accepted != 1 else ''} before"
        weight = self.weight(method, confidence)
        learned = f"{round(weight.rate * 100)}% ({weight.accepted} of {weight.total} confirmed)" if weight else ""
        return review, learned


def build_context(decisions) -> LearningContext:
    """Replay decisions in time order. ``decisions`` are objects with
    value_1, value_2, decision, method, confidence and created_at."""
    context = LearningContext()
    for decision in decisions:
        key = pair_key(decision.value_1, decision.value_2)
        if not key.strip("|"):
            continue
        history = context.pairs.setdefault(key, PairHistory())
        if decision.decision == "reset":
            context.pairs[key] = PairHistory()
            continue
        if decision.decision == "accept":
            history.accepted += 1
            history.rejected = False
        elif decision.decision == "reject":
            history.rejected = True
            created = getattr(decision, "created_at", None)
            history.rejected_on = created.date().isoformat() if created else ""
        method = getattr(decision, "method", None)
        if method and decision.decision in {"accept", "reject"}:
            band = confidence_band(method, decision.confidence)
            weight = context.methods.setdefault(method, {}).setdefault(band, MethodWeight())
            if decision.decision == "accept":
                weight.accepted += 1
            else:
                weight.rejected += 1
    return context
