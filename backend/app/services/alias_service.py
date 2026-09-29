"""Organization aliases and reviewer decisions.

Reviewers accept or reject proposed matches between two values (for example
two spellings of a vendor). Decisions are stored as evidence. A value pair is
*suggested* as an alias only when it was accepted in at least two different
reconciliations and never rejected; a suggestion becomes an active alias only
when a person approves it. Nothing is learned or activated automatically.

A "reset" decision withdraws the earlier decisions on a pair (for example a
rejection made by mistake); only decisions after the last reset count.
Decisions on keyless passes (amount/date pairs) are evidence for learned rule
weights, never for name aliases.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.alias import EntityAlias, MatchDecision
from app.reconciliation_engine.learning import pair_key, value_key  # noqa: F401  (re-exported)
from app.services.audit_service import AuditActor, record_event

MIN_ACCEPTANCES = 2
DECISIONS = {"accept": "Accepted", "reject": "Rejected", "reset": "Withdrew earlier decisions on"}


class AliasConflict(ValueError):
    pass


def is_name_evidence(decision: MatchDecision) -> bool:
    """Only key/name matches can become aliases (older decisions have no method)."""
    return not decision.method or decision.method.lower().startswith("similar key")


def record_decision(
    db: Session,
    *,
    scope_id: str,
    actor: AuditActor,
    value_1: str,
    value_2: str,
    decision: str,
    job_id: str | None = None,
    column_hint: str = "",
    confidence: int | None = None,
    note: str = "",
    method: str | None = None,
) -> MatchDecision:
    if decision not in DECISIONS:
        raise ValueError("Decision must be 'accept', 'reject' or 'reset'.")
    if not value_key(value_1) or not value_key(value_2):
        raise ValueError("Both values are required.")
    row = MatchDecision(
        scope_id=scope_id,
        job_id=job_id,
        column_hint=column_hint[:200],
        value_1=value_1[:300],
        value_2=value_2[:300],
        pair_key=pair_key(value_1, value_2)[:620],
        decision=decision,
        confidence=confidence,
        note=note,
        decided_by=actor.actor_id,
        method=(method or "").strip()[:80] or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    record_event(
        scope_id=scope_id, actor=actor, action=f"match.{decision}{'' if decision == 'reset' else 'ed'}",
        entity_type="match_decision", entity_id=row.id,
        summary=f"{DECISIONS[decision]} match: '{value_1}' ↔ '{value_2}'",
        after={"value_1": value_1, "value_2": value_2, "decision": decision, "confidence": confidence, "note": note, "method": method},
        metadata={"job_id": job_id, "column": column_hint},
    )
    return row


def _active_alias_keys(db: Session, scope_id: str) -> set[str]:
    rows = db.query(EntityAlias).filter(EntityAlias.scope_id == scope_id, EntityAlias.active.is_(True)).all()
    return {"||".join(sorted((row.canonical_key, row.variant_key))) for row in rows}


@dataclass
class Suggestion:
    pair_key: str
    value_1: str
    value_2: str
    acceptances: int
    jobs: int
    column_hint: str


def suggestions(db: Session, scope_id: str) -> list[Suggestion]:
    decisions = db.query(MatchDecision).filter(MatchDecision.scope_id == scope_id).order_by(MatchDecision.created_at).all()
    by_pair: dict[str, list[MatchDecision]] = {}
    for decision in decisions:
        if decision.decision == "reset":
            by_pair[decision.pair_key] = []  # Only decisions after a reset count.
        elif is_name_evidence(decision):
            by_pair.setdefault(decision.pair_key, []).append(decision)
    existing = _active_alias_keys(db, scope_id)
    result = []
    for key, rows in by_pair.items():
        left, _, right = key.partition("||")
        if not rows or key in existing or left == right:
            continue  # Already an alias, or already equal without one.
        if any(row.decision == "reject" for row in rows):
            continue  # One rejection blocks the suggestion (until a reset).
        accepted = [row for row in rows if row.decision == "accept"]
        jobs = {row.job_id or row.id for row in accepted}
        if len(accepted) >= MIN_ACCEPTANCES and len(jobs) >= MIN_ACCEPTANCES:
            latest = accepted[-1]
            result.append(Suggestion(key, latest.value_1, latest.value_2, len(accepted), len(jobs), latest.column_hint))
    return result


def create_alias(
    db: Session,
    *,
    scope_id: str,
    actor: AuditActor,
    canonical: str,
    variant: str,
    column_hint: str = "",
    source: str = "manual",
) -> EntityAlias:
    canonical, variant = canonical.strip(), variant.strip()
    canonical_key, variant_key = value_key(canonical), value_key(variant)
    if not canonical_key or not variant_key:
        raise ValueError("Both the name and its alternative spelling are required.")
    if canonical_key == variant_key:
        raise ValueError("These values already match through the built-in normalization; no alias is needed.")
    active = db.query(EntityAlias).filter(EntityAlias.scope_id == scope_id, EntityAlias.active.is_(True))
    for row in active.all():
        if row.variant_key == variant_key and row.canonical_key != canonical_key:
            raise AliasConflict(f"'{variant}' is already an alias of '{row.canonical}'. Remove that alias first.")
        if {row.variant_key, row.canonical_key} == {variant_key, canonical_key}:
            return row  # Same equivalence already exists.
    alias = EntityAlias(
        scope_id=scope_id,
        canonical=canonical[:300],
        variant=variant[:300],
        canonical_key=canonical_key[:300],
        variant_key=variant_key[:300],
        column_hint=column_hint[:200],
        source=source,
        created_by=actor.actor_id,
    )
    db.add(alias)
    db.commit()
    db.refresh(alias)
    record_event(
        scope_id=scope_id, actor=actor, action="alias.created", entity_type="alias", entity_id=alias.id,
        summary=f"Alias added: '{variant}' = '{canonical}'",
        after={"canonical": canonical, "variant": variant, "column": column_hint, "source": source},
    )
    return alias


def deactivate_alias(db: Session, *, scope_id: str, actor: AuditActor, alias_id: str) -> EntityAlias | None:
    alias = db.query(EntityAlias).filter(EntityAlias.id == alias_id, EntityAlias.scope_id == scope_id).first()
    if alias is None:
        return None
    if alias.active:
        alias.active = False
        db.commit()
        record_event(
            scope_id=scope_id, actor=actor, action="alias.removed", entity_type="alias", entity_id=alias.id,
            summary=f"Alias removed: '{alias.variant}' = '{alias.canonical}'",
            before={"active": True}, after={"active": False},
        )
    return alias
