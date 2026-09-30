"""Auto-resolution rules: storage, audit, preview and recurring-exception
suggestions. Matching-time evaluation lives in the engine
(``app.reconciliation_engine.resolution``)."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.resolution import ExceptionPattern, ResolutionRule
from app.reconciliation_engine.cache import normalize_header, to_number
from app.reconciliation_engine.learning import LearningContext
from app.reconciliation_engine.resolution import CATEGORIES, Evaluator, compile_rule, describe_rule, text_signature
from app.services.audit_service import AuditActor, record_event
from app.utils.timestamps import iso_utc

MIN_JOBS_FOR_SUGGESTION = 3
MAX_SIGNATURES_PER_JOB = 200
_AMOUNT_WORDS = ("AMOUNT", "AMT", "VALUE", "DEBIT", "CREDIT", "TOTAL")


class RuleNotFound(LookupError):
    pass


def rule_config(rule: ResolutionRule) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "version": rule.version,
        "category": rule.category,
        "conditions": json.loads(rule.conditions_json),
        "action": json.loads(rule.action_json),
        "enabled": rule.enabled,
    }


def rule_out(rule: ResolutionRule) -> dict:
    config = rule_config(rule)
    try:
        description = describe_rule(compile_rule(config))
    except ValueError as exc:  # Stored before a validation change: shown, never run.
        description = f"Invalid rule: {exc}"
    return {
        **config,
        "description": rule.description,
        "summary": description,
        "priority": rule.priority,
        "archived": rule.archived,
        "category_label": CATEGORIES.get(rule.category, rule.category),
        "created_by": rule.created_by,
        "updated_by": rule.updated_by,
        "created_at": iso_utc(rule.created_at),
        "updated_at": iso_utc(rule.updated_at),
    }


def _audit_state(rule: ResolutionRule) -> dict:
    return {
        "name": rule.name, "category": rule.category, "conditions": json.loads(rule.conditions_json),
        "action": json.loads(rule.action_json), "priority": rule.priority, "enabled": rule.enabled,
        "archived": rule.archived, "version": rule.version,
    }


def _validated(payload: dict) -> dict:
    compiled = compile_rule(payload)  # Raises ValueError with a plain message.
    return {
        "name": compiled.name,
        "category": compiled.category,
        "conditions": [
            {key: value for key, value in (("column", c.column), ("operator", c.operator), ("value", c.value), ("value_2", c.value_2)) if value not in (None, "")}
            for c in compiled.conditions
        ],
        "action": {
            "resolution": compiled.resolution, "reason_code": compiled.reason_code,
            "gl_account": compiled.gl_account, "note": compiled.note,
        },
    }


def list_rules(db: Session, scope_id: str, include_archived: bool = False) -> list[ResolutionRule]:
    query = db.query(ResolutionRule).filter(ResolutionRule.scope_id == scope_id)
    if not include_archived:
        query = query.filter(ResolutionRule.archived.is_(False))
    return query.order_by(ResolutionRule.priority, ResolutionRule.created_at).all()


def get_rule(db: Session, scope_id: str, rule_id: str) -> ResolutionRule:
    rule = db.query(ResolutionRule).filter(ResolutionRule.id == rule_id, ResolutionRule.scope_id == scope_id).first()
    if rule is None:
        raise RuleNotFound(rule_id)
    return rule


def create_rule(db: Session, *, scope_id: str, actor: AuditActor, payload: dict) -> ResolutionRule:
    clean = _validated(payload)
    priority = payload.get("priority")
    if priority is None:
        last = list_rules(db, scope_id)
        priority = (last[-1].priority + 10) if last else 10
    rule = ResolutionRule(
        scope_id=scope_id,
        name=clean["name"],
        description=str(payload.get("description") or "")[:2000],
        category=clean["category"],
        conditions_json=json.dumps(clean["conditions"]),
        action_json=json.dumps(clean["action"]),
        priority=int(priority),
        enabled=bool(payload.get("enabled", True)),
        created_by=actor.actor_id,
        updated_by=actor.actor_id,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    record_event(
        scope_id=scope_id, actor=actor, action="rule.created", entity_type="resolution_rule", entity_id=rule.id,
        summary=f"Auto-resolution rule created: {rule.name}", after=_audit_state(rule),
    )
    return rule


def update_rule(db: Session, *, scope_id: str, actor: AuditActor, rule_id: str, payload: dict) -> ResolutionRule:
    rule = get_rule(db, scope_id, rule_id)
    before = _audit_state(rule)
    merged = {**rule_config(rule), **{key: value for key, value in payload.items() if value is not None}}
    clean = _validated(merged)
    rule.name, rule.category = clean["name"], clean["category"]
    rule.conditions_json, rule.action_json = json.dumps(clean["conditions"]), json.dumps(clean["action"])
    if payload.get("description") is not None:
        rule.description = str(payload["description"])[:2000]
    if payload.get("priority") is not None:
        rule.priority = int(payload["priority"])
    if payload.get("enabled") is not None:
        rule.enabled = bool(payload["enabled"])
    after = _audit_state(rule)
    if {k: v for k, v in after.items() if k != "version"} == {k: v for k, v in before.items() if k != "version"}:
        return rule  # Nothing changed: no new version.
    rule.version += 1
    rule.updated_by = actor.actor_id
    db.commit()
    db.refresh(rule)
    record_event(
        scope_id=scope_id, actor=actor, action="rule.updated", entity_type="resolution_rule", entity_id=rule.id,
        summary=f"Auto-resolution rule changed: {rule.name} (now v{rule.version})", before=before, after=_audit_state(rule),
    )
    return rule


def archive_rule(db: Session, *, scope_id: str, actor: AuditActor, rule_id: str) -> ResolutionRule:
    rule = get_rule(db, scope_id, rule_id)
    if not rule.archived:
        before = _audit_state(rule)
        rule.archived, rule.enabled = True, False
        rule.version += 1
        rule.updated_by = actor.actor_id
        db.commit()
        record_event(
            scope_id=scope_id, actor=actor, action="rule.archived", entity_type="resolution_rule", entity_id=rule.id,
            summary=f"Auto-resolution rule removed: {rule.name}", before=before, after=_audit_state(rule),
        )
    return rule


def reorder_rules(db: Session, *, scope_id: str, actor: AuditActor, rule_ids: list[str]) -> list[ResolutionRule]:
    rules = {rule.id: rule for rule in list_rules(db, scope_id)}
    if set(rule_ids) != set(rules):
        raise ValueError("Send every active rule exactly once to change the order.")
    before = [rules[rule_id].name for rule_id in sorted(rules, key=lambda item: rules[item].priority)]
    for position, rule_id in enumerate(rule_ids, start=1):
        rules[rule_id].priority = position * 10
    db.commit()
    record_event(
        scope_id=scope_id, actor=actor, action="rule.reordered", entity_type="resolution_rule", entity_id=None,
        summary="Auto-resolution rule order changed", before={"order": before},
        after={"order": [rules[rule_id].name for rule_id in rule_ids]},
    )
    return list_rules(db, scope_id)


def active_rule_configs(db: Session, scope_id: str) -> list[dict]:
    """Enabled, valid rules in priority order, as the engine takes them."""
    configs = []
    for rule in list_rules(db, scope_id):
        if not rule.enabled:
            continue
        config = rule_config(rule)
        try:
            compile_rule(config)
        except ValueError:
            continue  # Never run a rule that no longer validates.
        configs.append(config)
    return configs


# ── Preview against a finished run ────────────────────────────────────────
def preview(rule_payload: dict, report_data: dict, learning: LearningContext | None, limit: int = 25) -> dict:
    """What a rule would resolve in an existing run's report data. The data
    holds the report's columns only, so a condition on a column that was not
    part of that run's setup finds nothing here (it can still match when the
    column is in the file)."""
    evaluator = Evaluator([compile_rule({**rule_payload, "enabled": True})], learning)
    category = evaluator.rules[0].category
    matches: list[dict] = []
    if category in {"only_in_source", "only_in_destination"}:
        section = "missing_in_file_2" if category == "only_in_source" else "missing_in_file_1"
        side = "source" if category == "only_in_source" else "destination"
        for record in report_data.get(section, []):
            if record.get("IDENTITY CLASSIFICATION") == "AMBIGUOUS_MATCH":
                continue
            if evaluator.unmatched(record, side):
                matches.append(record)
    elif category == "field_difference":
        for record in report_data.get("exceptions", []):
            if evaluator.difference(record.get("Field", ""), record.get("File 1 Value"), record.get("File 2 Value"), record.get("Difference"), record):
                matches.append(record)
    else:
        for record in report_data.get("identity_resolution", []):
            if record.get("IDENTITY CLASSIFICATION") != "EXCEPTION_MATCH":
                continue
            confidence = int(str(record.get("MATCH CONFIDENCE", "0")).rstrip("%") or 0)
            if evaluator.confirmation(record, record.get("MATCH KEY"), record.get("CANDIDATE KEY"), confidence, record.get("MATCH METHOD") or record.get("MATCH PASS") or ""):
                matches.append(record)
    visible = [
        {key: value for key, value in record.items() if not str(key).startswith(("NORM_", "__", "File Pair ID", "Sheet Rule ID"))}
        for record in matches[:limit]
    ]
    return {"matches": len(matches), "sample": visible, "summary": describe_rule(evaluator.rules[0])}


# ── Recurring exceptions ──────────────────────────────────────────────────
def _amount(record: dict) -> tuple[str, float] | None:
    """The record's largest amount-like value and its column."""
    values = [
        (normalize_header(key), abs(number))
        for key, value in record.items()
        if any(word in normalize_header(key) for word in _AMOUNT_WORDS)
        and not str(key).startswith(("NORM_", "__"))
        and (number := to_number(value)) is not None
    ]
    return max(values, key=lambda item: item[1]) if values else None


def _key_columns(report_data: dict) -> set[str]:
    columns = set(normalize_header(key) for key in (report_data.get("metadata") or {}).get("matching_keys") or [])
    for rule in report_data.get("sheet_rules") or []:
        columns.update(normalize_header(key) for key in rule.get("primary_key_source") or [])
        columns.update(normalize_header(key) for key in rule.get("primary_key_destination") or [])
    return columns


def record_patterns(db: Session, scope_id: str, job_id: str, report_data: dict) -> int:
    """Count unresolved unmatched records by column and wording, once per job."""
    keys = _key_columns(report_data)
    seen: dict[tuple[str, str, str], dict] = {}
    for category, section in (("only_in_source", "missing_in_file_2"), ("only_in_destination", "missing_in_file_1")):
        for record in report_data.get(section, []):
            if record.get("IDENTITY CLASSIFICATION") == "AMBIGUOUS_MATCH":
                continue
            amount = _amount(record)
            for column, value in record.items():
                name = normalize_header(column)
                if not name or name in keys or name.startswith(("ROW", "NORM_", "__", "MATCH", "IDENTITY", "GROUPED", "SHEET", "FILE PAIR")):
                    continue
                signature = text_signature(value)
                if signature is None:
                    continue
                entry = seen.setdefault((category, name, signature), {"count": 0, "max": None, "column": None, "example": str(value)[:300]})
                entry["count"] += 1
                if amount is not None and (entry["max"] is None or amount[1] > entry["max"]):
                    entry["column"], entry["max"] = amount
    now = datetime.now(timezone.utc)
    for (category, column, signature), entry in list(seen.items())[:MAX_SIGNATURES_PER_JOB]:
        pattern = (
            db.query(ExceptionPattern)
            .filter_by(scope_id=scope_id, category=category, column_name=column, signature=signature)
            .first()
        )
        if pattern is None:
            pattern = ExceptionPattern(scope_id=scope_id, category=category, column_name=column, signature=signature, jobs_seen=0, occurrences=0)
            db.add(pattern)
        if pattern.last_job_id != job_id:
            pattern.jobs_seen += 1
            pattern.occurrences += entry["count"]
        pattern.example = entry["example"]
        if entry["max"] is not None and entry["max"] >= (pattern.max_abs_amount or 0.0):
            pattern.max_abs_amount, pattern.amount_column = entry["max"], entry["column"]
        pattern.last_job_id = job_id
        pattern.last_seen_at = now
    db.commit()
    return len(seen)


def _covered(pattern: ExceptionPattern, rules: list[ResolutionRule]) -> bool:
    for rule in rules:
        if rule.category != pattern.category:
            continue
        for condition in json.loads(rule.conditions_json):
            column = normalize_header(condition.get("column", ""))
            if condition.get("operator") in {"contains", "equals", "starts_with"} and column in {pattern.column_name, "*"}:
                if str(condition.get("value", "")).casefold() in pattern.signature:
                    return True
    return False


def pattern_suggestions(db: Session, scope_id: str) -> list[dict]:
    rules = list_rules(db, scope_id)
    patterns = (
        db.query(ExceptionPattern)
        .filter(ExceptionPattern.scope_id == scope_id, ExceptionPattern.jobs_seen >= MIN_JOBS_FOR_SUGGESTION)
        .order_by(ExceptionPattern.jobs_seen.desc(), ExceptionPattern.occurrences.desc())
        .limit(50)
        .all()
    )
    result = []
    for pattern in patterns:
        if _covered(pattern, rules):
            continue
        conditions = [{"column": pattern.column_name, "operator": "contains", "value": pattern.signature}]
        if pattern.max_abs_amount and pattern.amount_column:
            # Capped at the largest amount seen, so a much larger item with
            # the same wording is still reviewed by a person.
            conditions.append({"column": pattern.amount_column, "operator": "abs_lte", "value": float(math.ceil(pattern.max_abs_amount))})
        where = "only in the source file" if pattern.category == "only_in_source" else "only in the destination file"
        result.append({
            "kind": "recurring_exception",
            "title": f"'{pattern.signature}' in {pattern.column_name} keeps appearing {where}",
            "detail": f"Seen in {pattern.jobs_seen} reconciliations ({pattern.occurrences} records)"
            + (f", up to {pattern.max_abs_amount:,.2f}" if pattern.max_abs_amount else "")
            + f". Example: {pattern.example}",
            "evidence": {"jobs": pattern.jobs_seen, "records": pattern.occurrences, "max_amount": pattern.max_abs_amount},
            "rule": {
                "name": pattern.signature.title()[:120],
                "category": pattern.category,
                "conditions": conditions,
                "action": {"resolution": pattern.signature.capitalize()[:120]},
            },
        })
    return result
