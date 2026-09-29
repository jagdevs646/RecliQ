"""Learning from reviewers' decisions: rule weights and remembered pairings.

Everything here is derived from the append-only ``match_decisions`` table, so
it can always be recomputed and explained ("18 of 20 confirmed"). Learning
proposes; people decide. A suggestion never edits a setup or a rule.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.alias import MatchDecision
from app.reconciliation_engine.learning import (
    MIN_EVIDENCE,
    LearningContext,
    build_context as build_from_decisions,
    wilson_lower_bound,
)

# A method is suggested for auto-confirmation only with this much evidence.
AUTO_CONFIRM_MIN_DECISIONS = 10
AUTO_CONFIRM_MIN_LOWER_BOUND = 0.9
WEAK_METHOD_MAX_RATE = 0.5


def _decisions(db: Session, scope_id: str) -> list[MatchDecision]:
    return (
        db.query(MatchDecision)
        .filter(MatchDecision.scope_id == scope_id)
        .order_by(MatchDecision.created_at, MatchDecision.id)
        .all()
    )


def build_context(db: Session, scope_id: str) -> LearningContext:
    return build_from_decisions(_decisions(db, scope_id))


def method_weights(context: LearningContext) -> list[dict]:
    rows = []
    for method, bands in context.methods.items():
        for band, weight in bands.items():
            rows.append({
                "method": method,
                "band": band,
                "accepted": weight.accepted,
                "rejected": weight.rejected,
                "total": weight.total,
                "acceptance_rate": round(weight.rate, 4),
                "lower_bound": round(wilson_lower_bound(weight.accepted, weight.total), 4),
                "enough_evidence": weight.total >= MIN_EVIDENCE,
            })
    return sorted(rows, key=lambda row: (row["method"], row["band"]))


def remembered_rejections(db: Session, scope_id: str) -> list[dict]:
    """Pairings not proposed again, with the latest rejection's details."""
    context = build_context(db, scope_id)
    latest: dict[str, MatchDecision] = {}
    for decision in _decisions(db, scope_id):
        if decision.decision == "reject":
            latest[decision.pair_key] = decision
    rows = []
    for key, decision in latest.items():
        history = context.pairs.get(key)
        if history and history.rejected:
            rows.append({
                "value_1": decision.value_1,
                "value_2": decision.value_2,
                "method": decision.method,
                "job_id": decision.job_id,
                "rejected_on": history.rejected_on,
            })
    return sorted(rows, key=lambda row: row["rejected_on"], reverse=True)


def _band_floor(band: str) -> int | None:
    head = band.split("-", 1)[0]
    return int(head) if head.isdigit() else None


def suggestions(db: Session, scope_id: str) -> list[dict]:
    """Evidence-based proposals. Each carries the numbers behind it and, when
    it can be acted on with a rule, a prefilled rule for a person to review."""
    context = build_context(db, scope_id)
    result: list[dict] = []
    for row in method_weights(context):
        method, band, total = row["method"], row["band"], row["total"]
        evidence = f"{row['accepted']} of {total} confirmed"
        similar = method.lower().startswith("similar key")
        where = f"'{method}'" + (f" scoring {band}" if band != "all" else "")
        if total >= AUTO_CONFIRM_MIN_DECISIONS and row["lower_bound"] >= AUTO_CONFIRM_MIN_LOWER_BOUND:
            conditions = [{"operator": "method_is", "value": method}]
            floor = _band_floor(band) if similar else None
            conditions.append({"operator": "confidence_gte", "value": max(50, floor) if floor is not None else 90})
            result.append({
                "kind": "auto_confirm_method",
                "title": f"Reviewers almost always confirm {where}",
                "detail": f"{evidence}. You could let a rule confirm these automatically; every confirmation stays in the report and audit log.",
                "evidence": row,
                "rule": {
                    "name": f"Confirm {method}" + (f" ≥ {floor}%" if floor is not None else ""),
                    "category": "to_confirm",
                    "conditions": conditions,
                    "action": {"resolution": "Confirmed by rule", "reason_code": "LEARNED"},
                },
            })
        elif total >= MIN_EVIDENCE and row["acceptance_rate"] <= WEAK_METHOD_MAX_RATE:
            if similar and band != "all":
                floor = _band_floor(band)
                advice = (
                    f"Raise the similar-key threshold above {floor + 4}% for this setup."
                    if floor is not None else "Raise the similar-key threshold for this setup."
                )
            else:
                advice = "Tighten this pass (smaller date window or tolerance, or require a reference) or remove it."
            result.append({
                "kind": "weak_method",
                "title": f"Reviewers often reject {where}",
                "detail": f"Only {evidence}. {advice}",
                "evidence": row,
                "rule": None,
            })

    repeat_pairs = sum(1 for history in context.pairs.values() if history.accepted >= 2 and not history.rejected)
    if repeat_pairs:
        result.append({
            "kind": "auto_confirm_repeat_pairs",
            "title": f"{repeat_pairs} pairing{'s were' if repeat_pairs != 1 else ' was'} confirmed in more than one run",
            "detail": "A rule can confirm a pairing automatically once a reviewer has confirmed the same pair before. "
                      "A pair that was ever rejected is never confirmed this way.",
            "evidence": {"pairs": repeat_pairs},
            "rule": {
                "name": "Confirm pairs reviewers confirmed twice",
                "category": "to_confirm",
                "conditions": [{"operator": "previously_confirmed", "value": 2}],
                "action": {"resolution": "Confirmed by rule (reviewed before)", "reason_code": "REPEAT"},
            },
        })
    return result
