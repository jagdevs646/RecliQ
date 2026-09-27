"""Saved, versioned reconciliation templates.

A template stores everything about a reconciliation except the files: file
pairs, sheet rules, keys, secondary keys, mappings, transformations, keyless
passes, normalization settings, thresholds and report settings. Every edit
creates a new immutable version.

Re-running a template on new files resolves the template's sheets and
columns against the new headers, in order of certainty:

1. identical header (after the usual case/space cleanup);
2. an alternative name saved in the template (``column_aliases``);
3. the same name once punctuation is ignored ("Invoice_No" = "Invoice No");
4. a known accounting synonym, only when exactly one column qualifies;
5. a very similar name (≥ 88%), only when it clearly beats every other column.

Anything still unresolved is reported for the user to map; nothing is guessed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.file import UploadedFile
from app.models.job import ReconciliationJob
from app.models.template import ReconciliationTemplate, ReconciliationTemplateVersion
from app.reconciliation_engine.cache import normalize_header
from app.reconciliation_engine.schema.semantic_mapper import get_synonyms, normalize_column_name_for_mapping
from app.schemas.reconciliation import (
    FilePairConfig,
    FileSource,
    GenericReconciliationRequest,
    ReconciliationPlan,
    SheetRuleConfig,
    normalize_legacy_request,
)
from app.schemas.template import TemplateConfig, TemplateFilePair, TemplateRunRequest

SIMILAR_NAME_THRESHOLD = 88
SIMILAR_NAME_MARGIN = 5


class TemplateResolutionError(ValueError):
    def __init__(self, message: str, resolution: dict[str, Any]):
        super().__init__(message)
        self.resolution = resolution


# ── Building a template config ────────────────────────────────────────────
def config_from_plan(db: Session, session_id: str, plan: ReconciliationPlan, report_settings: dict | None = None) -> TemplateConfig:
    names: dict[str, str] = {}

    def filename(file_id: str) -> str:
        if file_id not in names:
            record = db.query(UploadedFile).filter(UploadedFile.id == file_id, UploadedFile.session_id == session_id).first()
            names[file_id] = record.original_filename if record else ""
        return names[file_id]

    pairs = []
    for index, pair in enumerate(plan.file_pairs, start=1):
        pair_id = pair.file_pair_id or f"file-pair-{index}"
        rules = []
        for rule_index, rule in enumerate(pair.sheet_rules, start=1):
            copy = rule.model_copy(deep=True)
            copy.sheet_rule_id = rule.sheet_rule_id or f"rule-{index}-{rule_index}"
            # Rules without explicit sheets read the file's configured sheet.
            if not copy.source_sheets:
                copy.source_sheets = [source.sheet_id for source in pair.source_files if source.sheet_id]
            if not copy.destination_sheets:
                copy.destination_sheets = [source.sheet_id for source in pair.destination_files if source.sheet_id]
            rules.append(copy)
        pairs.append(
            TemplateFilePair(
                file_pair_id=pair_id,
                label=str(pair.report_metadata.get("label") or ""),
                source_filename=filename(pair.source_files[0].file_id) if pair.source_files else "",
                destination_filename=filename(pair.destination_files[0].file_id) if pair.destination_files else "",
                sheet_rules=rules,
                report_metadata=pair.report_metadata,
            )
        )
    return TemplateConfig(orientation=plan.orientation, file_pairs=pairs, report_settings=report_settings or {})


def config_from_job(db: Session, session_id: str, job_id: str) -> TemplateConfig:
    job = db.query(ReconciliationJob).filter(ReconciliationJob.id == job_id, ReconciliationJob.session_id == session_id).first()
    if job is None:
        raise LookupError("Job not found")
    if job.job_type != "generic":
        raise ValueError("Only general reconciliations can be saved as templates.")
    plan = ReconciliationPlan.model_validate(json.loads(job.settings_json or "{}"))
    return config_from_plan(db, session_id, plan)


def config_from_request(db: Session, session_id: str, request: GenericReconciliationRequest, report_settings: dict | None = None) -> TemplateConfig:
    return config_from_plan(db, session_id, normalize_legacy_request(request), report_settings)


# ── Persistence ──────────────────────────────────────────────────────────
def create_template(db: Session, *, session_id: str, actor_id: str, name: str, description: str, config: TemplateConfig, change_note: str = "") -> ReconciliationTemplate:
    template = ReconciliationTemplate(session_id=session_id, name=name.strip(), description=description, current_version=1)
    db.add(template)
    db.flush()
    db.add(ReconciliationTemplateVersion(
        template_id=template.id, version=1, config_json=config.model_dump_json(), change_note=change_note or "Created", created_by=actor_id,
    ))
    db.commit()
    db.refresh(template)
    return template


def add_version(db: Session, template: ReconciliationTemplate, *, actor_id: str, config: TemplateConfig, change_note: str) -> ReconciliationTemplateVersion:
    version = ReconciliationTemplateVersion(
        template_id=template.id,
        version=template.current_version + 1,
        config_json=config.model_dump_json(),
        change_note=change_note or "Updated",
        created_by=actor_id,
    )
    template.current_version = version.version
    db.add(version)
    db.commit()
    db.refresh(template)
    return version


def get_template(db: Session, session_id: str, template_id: str, include_archived: bool = False) -> ReconciliationTemplate | None:
    query = db.query(ReconciliationTemplate).filter(ReconciliationTemplate.id == template_id, ReconciliationTemplate.session_id == session_id)
    if not include_archived:
        query = query.filter(ReconciliationTemplate.archived.is_(False))
    return query.first()


def version_config(db: Session, template: ReconciliationTemplate, version: int | None = None) -> tuple[int, TemplateConfig]:
    number = version or template.current_version
    row = (
        db.query(ReconciliationTemplateVersion)
        .filter(ReconciliationTemplateVersion.template_id == template.id, ReconciliationTemplateVersion.version == number)
        .first()
    )
    if row is None:
        raise LookupError(f"Version {number} not found")
    return number, TemplateConfig.model_validate_json(row.config_json)


def summarize(config: TemplateConfig) -> dict[str, Any]:
    rules = [rule for pair in config.file_pairs for rule in pair.sheet_rules]
    return {
        "file_pairs": len(config.file_pairs),
        "sheet_rules": len(rules),
        "keys": sorted({" + ".join(rule.matching_strategy.primary_key_source) for rule in rules if rule.matching_strategy.primary_key_source}),
        "pairs": [
            {"file_pair_id": pair.file_pair_id, "label": pair.label, "source_filename": pair.source_filename,
             "destination_filename": pair.destination_filename, "sheet_rules": len(pair.sheet_rules)}
            for pair in config.file_pairs
        ],
    }


# ── Resolution against new files ────────────────────────────────────────
def _compact(name: str) -> str:
    return normalize_column_name_for_mapping(name).replace(" ", "")


def resolve_name(wanted: str, available: list[str], aliases: list[str] | None = None) -> tuple[str | None, str]:
    """Find ``wanted`` among ``available`` headers. Returns (match, method)."""
    by_header = {normalize_header(column): column for column in available}
    target = normalize_header(wanted)
    if target in by_header:
        return by_header[target], "same name"
    for alias in aliases or []:
        if normalize_header(alias) in by_header:
            return by_header[normalize_header(alias)], "saved alternative name"
    compact = {_compact(column): column for column in available}
    if _compact(wanted) in compact:
        return compact[_compact(wanted)], "same name, different punctuation"
    # A known accounting synonym ("Vendor" -> "Party Name") or a name holding
    # every word of the other ("Invoice" -> "Invoice No", "Date" -> "Txn Date").
    # Either is accepted only when exactly one column qualifies by both rules
    # together: "Amount" with both "Net Amount" and "Gross Amount" is asked.
    synonyms = set(get_synonyms(normalize_column_name_for_mapping(wanted))) - {normalize_column_name_for_mapping(wanted)}
    synonym_hits = [column for column in available if normalize_column_name_for_mapping(column) in synonyms]
    wanted_words = set(normalize_column_name_for_mapping(wanted).split())
    contained = [
        column
        for column in available
        if wanted_words
        and (words := set(normalize_column_name_for_mapping(column).split()))
        and (wanted_words <= words or words <= wanted_words)
    ]
    candidates = list(dict.fromkeys(synonym_hits + contained))
    if len(candidates) == 1:
        return candidates[0], "accounting synonym" if synonym_hits else "name contains the saved name"
    from rapidfuzz import fuzz

    scored = sorted(
        ((fuzz.ratio(normalize_column_name_for_mapping(wanted), normalize_column_name_for_mapping(column)), column) for column in available),
        reverse=True,
    )
    if scored and scored[0][0] >= SIMILAR_NAME_THRESHOLD and (len(scored) == 1 or scored[0][0] - scored[1][0] >= SIMILAR_NAME_MARGIN):
        return scored[0][1], f"similar name ({int(scored[0][0])}%)"
    return None, "not found"


def _rule_columns(rule: SheetRuleConfig) -> dict[str, list[str]]:
    """Every column a rule reads, per side (derived transformation outputs excluded)."""
    strategy = rule.matching_strategy
    derived = {"source": set(), "destination": set()}
    columns: dict[str, list[str]] = {"source": [], "destination": []}
    for step in rule.transformations:
        sides = ["source", "destination"] if step.side == "both" else [step.side]
        for side in sides:
            if step.operation == "debit_credit_to_signed":
                columns[side] += [step.params.get("debit_column", ""), step.params.get("credit_column", "")]
                derived[side].add(normalize_header(step.output_column or "SIGNED AMOUNT"))
            else:
                columns[side] += step.columns
    columns["source"] += strategy.primary_key_source + [c.source_column for c in strategy.secondary_conditions]
    columns["destination"] += strategy.primary_key_destination + [c.destination_column for c in strategy.secondary_conditions]
    for mapping in rule.reconciliation_mapping:
        columns["source"] += mapping.file_1_fields
        columns["destination"] += mapping.file_2_fields
    columns["source"] += rule.include_columns_file_1
    columns["destination"] += rule.include_columns_file_2
    for item in strategy.matching_passes:
        columns["source"] += [value for value in (item.amount_source, item.date_source, item.narrative_source) if value]
        columns["destination"] += [value for value in (item.amount_destination, item.date_destination, item.narrative_destination) if value]
    return {
        side: [column for column in dict.fromkeys(normalize_header(value) for value in values if value) if column not in derived[side]]
        for side, values in columns.items()
    }


def _rename_rule(rule: SheetRuleConfig, mapping: dict[str, dict[str, str]]) -> SheetRuleConfig:
    """Copy a rule with its column names replaced by the resolved headers."""
    data = rule.model_dump()

    def rename(side: str, value: str | None) -> str | None:
        if not value:
            return value
        return mapping[side].get(normalize_header(value), value)

    strategy = data["matching_strategy"]
    strategy["primary_key_source"] = [rename("source", v) for v in strategy["primary_key_source"]]
    strategy["primary_key_destination"] = [rename("destination", v) for v in strategy["primary_key_destination"]]
    for condition in strategy["secondary_conditions"]:
        condition["source_column"] = rename("source", condition["source_column"])
        condition["destination_column"] = rename("destination", condition["destination_column"])
    for item in strategy["matching_passes"]:
        for field in ("amount_source", "date_source", "narrative_source"):
            item[field] = rename("source", item[field])
        for field in ("amount_destination", "date_destination", "narrative_destination"):
            item[field] = rename("destination", item[field])
    for item in data["reconciliation_mapping"]:
        item["file_1_fields"] = [rename("source", v) for v in item["file_1_fields"]]
        item["file_2_fields"] = [rename("destination", v) for v in item["file_2_fields"]]
    data["include_columns_file_1"] = [rename("source", v) for v in data["include_columns_file_1"]]
    data["include_columns_file_2"] = [rename("destination", v) for v in data["include_columns_file_2"]]
    steps = []
    for step in data["transformations"]:
        # A "both" step may name a column that each new file spells
        # differently, so it becomes one step per file.
        for side in (["source", "destination"] if step["side"] == "both" else [step["side"]]):
            copy = {**step, "side": side, "params": dict(step["params"])}
            if step["operation"] == "debit_credit_to_signed":
                copy["params"]["debit_column"] = rename(side, step["params"].get("debit_column"))
                copy["params"]["credit_column"] = rename(side, step["params"].get("credit_column"))
            else:
                copy["columns"] = [rename(side, v) for v in step["columns"]]
            steps.append(copy)
    data["transformations"] = steps
    return SheetRuleConfig.model_validate(data)


def resolve_template(db: Session, session_id: str, config: TemplateConfig, request: TemplateRunRequest) -> tuple[ReconciliationPlan, dict[str, Any]]:
    """Map a template onto new uploads. Raises TemplateResolutionError when a
    sheet or column cannot be resolved with certainty."""
    from app.services.reconciliation_service import get_file_columns, get_file_metadata

    assignments = {item.file_pair_id: item for item in request.files}
    missing_pairs = [pair.file_pair_id for pair in config.file_pairs if pair.file_pair_id not in assignments]
    resolution: dict[str, Any] = {"file_pairs": [], "unresolved": []}
    if missing_pairs:
        resolution["unresolved"] = [{"file_pair_id": pair_id, "problem": "No files chosen for this file pair"} for pair_id in missing_pairs]
        raise TemplateResolutionError("Choose a source and destination file for every file pair.", resolution)

    file_pairs = []
    for pair in config.file_pairs:
        assignment = assignments[pair.file_pair_id]
        files = {"source": assignment.source_file_id, "destination": assignment.destination_file_id}
        sheets_available = {side: [sheet["id"] for sheet in get_file_metadata(db, file_id, session_id)] for side, file_id in files.items()}
        pair_report: dict[str, Any] = {"file_pair_id": pair.file_pair_id, "label": pair.label, "sheets": [], "rules": []}
        rules = []
        for rule in pair.sheet_rules:
            sheet_map: dict[str, list[str]] = {}
            for side, wanted_sheets in (("source", rule.source_sheets), ("destination", rule.destination_sheets)):
                resolved_sheets = []
                overrides = request.sheet_overrides.get(pair.file_pair_id, {}).get(side, {})
                wanted_list = wanted_sheets or [None]
                for wanted in wanted_list:
                    available = sheets_available[side]
                    if wanted in overrides and overrides[wanted] in available:
                        match, method = overrides[wanted], "chosen by you"
                    elif wanted is None:
                        match, method = available[0] if available else None, "first sheet"
                    else:
                        match, method = resolve_name(wanted, available)
                        if match is None and len(available) == 1 and len(wanted_list) == 1:
                            match, method = available[0], "only sheet in the file"
                    pair_report["sheets"].append({"side": side, "template": wanted, "resolved": match, "method": method})
                    if match is None:
                        resolution["unresolved"].append({"file_pair_id": pair.file_pair_id, "side": side, "sheet": wanted, "problem": "Sheet not found", "available": available})
                    else:
                        resolved_sheets.append(match)
                sheet_map[side] = resolved_sheets

            column_map: dict[str, dict[str, str]] = {"source": {}, "destination": {}}
            rule_report = {"sheet_rule_id": rule.sheet_rule_id, "columns": []}
            for side, wanted_columns in _rule_columns(rule).items():
                if not sheet_map[side]:
                    continue
                available = get_file_columns(db, files[side], session_id, sheet_id=sheet_map[side][0], orientation=config.orientation)
                overrides = {normalize_header(k): v for k, v in request.column_overrides.get(rule.sheet_rule_id or "", {}).get(side, {}).items()}
                used: dict[str, str] = {}
                for wanted in wanted_columns:
                    if wanted in overrides and normalize_header(overrides[wanted]) in {normalize_header(c) for c in available}:
                        match, method = normalize_header(overrides[wanted]), "chosen by you"
                    else:
                        match, method = resolve_name(wanted, available, config.column_aliases.get(wanted))
                        match = normalize_header(match) if match else None
                    if match and match in used and used[match] != wanted:
                        match, method = None, f"already used for {used[match]}"
                    rule_report["columns"].append({"side": side, "template": wanted, "resolved": match, "method": method})
                    if match is None:
                        resolution["unresolved"].append({
                            "file_pair_id": pair.file_pair_id, "sheet_rule_id": rule.sheet_rule_id, "side": side,
                            "column": wanted, "problem": "Column not found", "available": available,
                        })
                    else:
                        used[match] = wanted
                        column_map[side][wanted] = match
            pair_report["rules"].append(rule_report)
            renamed = _rename_rule(rule, column_map)
            renamed.source_sheets, renamed.destination_sheets = sheet_map["source"], sheet_map["destination"]
            rules.append(renamed)
        resolution["file_pairs"].append(pair_report)
        file_pairs.append(
            FilePairConfig(
                file_pair_id=pair.file_pair_id,
                source_files=[FileSource(file_id=files["source"])],
                destination_files=[FileSource(file_id=files["destination"])],
                sheet_rules=rules,
                report_metadata={**pair.report_metadata, "label": pair.label} if pair.label else pair.report_metadata,
            )
        )
    if resolution["unresolved"]:
        raise TemplateResolutionError("Some sheets or columns could not be matched automatically.", resolution)
    plan = ReconciliationPlan(
        file_pairs=file_pairs,
        orientation=config.orientation,
        report_metadata={"report_settings": config.report_settings},
        precheck_acknowledged=request.precheck_acknowledged,
        precheck_summary=request.precheck_summary,
    )
    plan.execution_rules()
    resolution["automatic"] = [
        item
        for pair in resolution["file_pairs"]
        for item in pair["sheets"] + [column for rule in pair["rules"] for column in rule["columns"]]
        if item["method"] not in {"same name", "first sheet"}
    ]
    return plan, resolution


def mark_run(db: Session, template: ReconciliationTemplate) -> None:
    template.last_run_at = datetime.now(timezone.utc)
    db.commit()
