from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class RuleMapping(BaseModel):
    file_1_fields: list[str] = Field(default_factory=list)
    file_2_fields: list[str] = Field(default_factory=list)


class FileSource(BaseModel):
    file_id: str
    sheet_id: str | None = None


class SecondaryMatchCondition(BaseModel):
    """A deterministic condition used to narrow non-exact primary-key candidates."""

    source_column: str
    destination_column: str
    comparison_method: Literal[
        "exact_text",
        "normalized_date",
        "numeric_tolerance",
        "matcher_based",
    ]
    numeric_tolerance: float | None = Field(default=None, ge=0)


class SimilarityPolicy(BaseModel):
    matcher_type_override: str | None = None
    threshold: int | None = Field(default=None, ge=0, le=100)


class MatchingStrategy(BaseModel):
    primary_key_source: list[str] = Field(default_factory=list)
    primary_key_destination: list[str] = Field(default_factory=list)
    secondary_conditions: list[SecondaryMatchCondition] = Field(default_factory=list)
    similarity_policy: SimilarityPolicy = Field(default_factory=SimilarityPolicy)
    date_only_override: bool = False


class SheetRuleConfig(BaseModel):
    """All matching and field-mapping choices for one explicit sheet relationship."""

    sheet_rule_id: str | None = None
    source_sheets: list[str] = Field(default_factory=list)
    destination_sheets: list[str] = Field(default_factory=list)
    matching_strategy: MatchingStrategy
    reconciliation_mapping: list[RuleMapping] = Field(default_factory=list)
    include_columns_file_1: list[str] = Field(default_factory=list)
    include_columns_file_2: list[str] = Field(default_factory=list)
    report_label: str = ""
    report_metadata: dict[str, Any] = Field(default_factory=dict)


class FilePairConfig(BaseModel):
    """An independently configured source/destination file relationship."""

    file_pair_id: str | None = None
    source_files: list[FileSource] = Field(default_factory=list)
    destination_files: list[FileSource] = Field(default_factory=list)
    sheet_rules: list[SheetRuleConfig] = Field(default_factory=list)
    report_metadata: dict[str, Any] = Field(default_factory=dict)


class ReconciliationPlan(BaseModel):
    """Canonical execution contract consumed by generic reconciliation jobs."""

    file_pairs: list[FilePairConfig] = Field(default_factory=list)
    orientation: str = "vertical"
    report_metadata: dict[str, Any] = Field(default_factory=dict)
    # Preserves the submitted contract for audit/debugging without making it an
    # execution path. All runtime code consumes ``file_pairs``.
    legacy: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def _sources_for_rule(files: list[FileSource], sheet_ids: list[str]) -> list[FileSource]:
        if not sheet_ids:
            return files
        already_scoped = [source for source in files if source.sheet_id in sheet_ids]
        if already_scoped:
            return already_scoped
        return [
            source.model_copy(update={"sheet_id": sheet_id})
            for source in files
            for sheet_id in sheet_ids
        ]

    def execution_rules(self) -> list[dict[str, Any]]:
        """Return independently executable file-pair/sheet-rule work items."""
        items: list[dict[str, Any]] = []
        for pair_index, file_pair in enumerate(self.file_pairs, start=1):
            if not file_pair.source_files or not file_pair.destination_files:
                raise ValueError(f"File pair {pair_index} requires source and destination files")

            file_pair_label = str(file_pair.report_metadata.get("label") or "").strip()
            for rule_index, rule in enumerate(file_pair.sheet_rules, start=1):
                source_files = self._sources_for_rule(file_pair.source_files, rule.source_sheets)
                destination_files = self._sources_for_rule(file_pair.destination_files, rule.destination_sheets)
                if not source_files or not destination_files:
                    raise ValueError(f"Sheet rule {rule_index} has no scoped source or destination sheets")
                items.append(
                    {
                        "source_files_1": [source.model_dump() for source in source_files],
                        "source_files_2": [source.model_dump() for source in destination_files],
                        "key_file_1": rule.matching_strategy.primary_key_source,
                        "key_file_2": rule.matching_strategy.primary_key_destination,
                        "rules": [mapping.model_dump() for mapping in rule.reconciliation_mapping],
                        "include_columns_file_1": rule.include_columns_file_1,
                        "include_columns_file_2": rule.include_columns_file_2,
                        "secondary_conditions": [
                            condition.model_dump()
                            for condition in rule.matching_strategy.secondary_conditions
                        ],
                        "similarity_policy": rule.matching_strategy.similarity_policy.model_dump(),
                        "date_only_override": rule.matching_strategy.date_only_override,
                        "file_pair_id": file_pair.file_pair_id or f"file-pair-{pair_index}",
                        "file_pair_label": file_pair_label,
                        "file_pair_index": pair_index,
                        "file_pair_count": len(self.file_pairs),
                        "sheet_rule_id": rule.sheet_rule_id or f"rule-{rule_index}",
                        "rule_index": rule_index,
                        "rule_count": len(file_pair.sheet_rules),
                        "report_label": rule.report_label,
                    }
                )
        return items

    def execution_pairs(self) -> list[dict[str, Any]]:
        """Backward-compatible alias for callers not yet renamed to rules."""
        return [
            {
                **item,
                "source_file_1": item["source_files_1"][0],
                "source_file_2": item["source_files_2"][0],
            }
            for item in self.execution_rules()
        ]


class SheetPairConfig(BaseModel):
    """Legacy single-sheet pair accepted by the pre-plan generic API."""

    source_file_1: FileSource
    source_file_2: FileSource
    key_file_1: str | list[str] | None = None
    key_file_2: str | list[str] | None = None
    rules: list[RuleMapping] = Field(default_factory=list)
    include_columns_file_1: list[str] = Field(default_factory=list)
    include_columns_file_2: list[str] = Field(default_factory=list)
    matching_strategy: MatchingStrategy | None = None
    reconciliation_mapping: list[RuleMapping] = Field(default_factory=list)
    report_label: str = ""


class GenericReconciliationRequest(BaseModel):
    file_1_id: str | None = None
    file_2_id: str | None = None

    # Backward-compatible flat fields.
    source_files_1: list[FileSource] = Field(default_factory=list)
    source_files_2: list[FileSource] = Field(default_factory=list)
    key_file_1: str | list[str] | None = None
    key_file_2: str | list[str] | None = None
    rules: list[RuleMapping] = Field(default_factory=list)
    include_columns_file_1: list[str] = Field(default_factory=list)
    include_columns_file_2: list[str] = Field(default_factory=list)
    pairs: list[SheetPairConfig] = Field(default_factory=list)

    # Canonical plan input. ``file_pairs`` is the convenient direct form;
    # ``plan`` is the explicit envelope for future versioned API clients.
    file_pairs: list[FilePairConfig] = Field(default_factory=list)
    plan: ReconciliationPlan | None = None
    orientation: str = "vertical"

    @model_validator(mode="after")
    def populate_file_sources(self) -> GenericReconciliationRequest:
        if not self.file_1_id and self.source_files_1:
            self.file_1_id = self.source_files_1[0].file_id
        if not self.file_2_id and self.source_files_2:
            self.file_2_id = self.source_files_2[0].file_id
        if self.file_1_id and not self.source_files_1:
            self.source_files_1 = [FileSource(file_id=self.file_1_id)]
        if self.file_2_id and not self.source_files_2:
            self.source_files_2 = [FileSource(file_id=self.file_2_id)]
        return self

    def to_reconciliation_plan(self) -> ReconciliationPlan:
        return normalize_legacy_request(self)


def _as_key_list(value: str | list[str] | None, field_name: str) -> list[str]:
    if isinstance(value, str):
        value = [value]
    keys = [str(item).strip() for item in (value or []) if str(item).strip()]
    if not keys:
        raise ValueError(f"{field_name} requires at least one primary-key column")
    return keys


def _rule_label(source_sheets: list[str], destination_sheets: list[str], index: int) -> str:
    source_label = ", ".join(source_sheets) if source_sheets else "Default sheet"
    destination_label = ", ".join(destination_sheets) if destination_sheets else "Default sheet"
    return f"Rule {index}: {source_label} -> {destination_label}"


def _legacy_pair_to_file_pair(pair: SheetPairConfig, index: int) -> FilePairConfig:
    source_sheets = [pair.source_file_1.sheet_id] if pair.source_file_1.sheet_id else []
    destination_sheets = [pair.source_file_2.sheet_id] if pair.source_file_2.sheet_id else []
    strategy = pair.matching_strategy or MatchingStrategy(
        primary_key_source=_as_key_list(pair.key_file_1, "key_file_1"),
        primary_key_destination=_as_key_list(pair.key_file_2, "key_file_2"),
    )
    mapping = pair.reconciliation_mapping or pair.rules
    label = pair.report_label or _rule_label(source_sheets, destination_sheets, 1)
    return FilePairConfig(
        file_pair_id=f"file-pair-{index}",
        source_files=[pair.source_file_1],
        destination_files=[pair.source_file_2],
        sheet_rules=[
            SheetRuleConfig(
                sheet_rule_id=f"rule-{index}-1",
                source_sheets=source_sheets,
                destination_sheets=destination_sheets,
                matching_strategy=strategy,
                reconciliation_mapping=mapping,
                include_columns_file_1=pair.include_columns_file_1,
                include_columns_file_2=pair.include_columns_file_2,
                report_label=label,
            )
        ],
    )


def normalize_legacy_request(
    payload: GenericReconciliationRequest | ReconciliationPlan | dict[str, Any],
) -> ReconciliationPlan:
    """Convert every supported generic request shape into one execution plan."""
    if isinstance(payload, ReconciliationPlan):
        return payload
    request = payload if isinstance(payload, GenericReconciliationRequest) else GenericReconciliationRequest.model_validate(payload)

    if request.plan is not None:
        return request.plan
    if request.file_pairs:
        return ReconciliationPlan(file_pairs=request.file_pairs, orientation=request.orientation)
    if request.pairs:
        return ReconciliationPlan(
            file_pairs=[_legacy_pair_to_file_pair(pair, index) for index, pair in enumerate(request.pairs, start=1)],
            orientation=request.orientation,
            legacy=request.model_dump(exclude={"plan", "file_pairs"}, exclude_none=True),
        )

    source_files = request.source_files_1 or ([FileSource(file_id=request.file_1_id)] if request.file_1_id else [])
    destination_files = request.source_files_2 or ([FileSource(file_id=request.file_2_id)] if request.file_2_id else [])
    if not source_files or not destination_files:
        raise ValueError("Generic reconciliation requires source and destination files")

    source_sheets = [source.sheet_id for source in source_files if source.sheet_id]
    destination_sheets = [source.sheet_id for source in destination_files if source.sheet_id]
    return ReconciliationPlan(
        orientation=request.orientation,
        legacy=request.model_dump(exclude={"plan", "file_pairs"}, exclude_none=True),
        file_pairs=[
            FilePairConfig(
                file_pair_id="file-pair-1",
                source_files=source_files,
                destination_files=destination_files,
                sheet_rules=[
                    SheetRuleConfig(
                        sheet_rule_id="rule-1-1",
                        source_sheets=source_sheets,
                        destination_sheets=destination_sheets,
                        matching_strategy=MatchingStrategy(
                            primary_key_source=_as_key_list(request.key_file_1, "key_file_1"),
                            primary_key_destination=_as_key_list(request.key_file_2, "key_file_2"),
                        ),
                        reconciliation_mapping=request.rules,
                        include_columns_file_1=request.include_columns_file_1,
                        include_columns_file_2=request.include_columns_file_2,
                        report_label=_rule_label(source_sheets, destination_sheets, 1),
                    )
                ],
            )
        ],
    )


class GSTReconciliationRequest(BaseModel):
    file_1_id: str | None = None
    file_2_id: str | None = None
    source_files_1: list[FileSource] = Field(default_factory=list)
    source_files_2: list[FileSource] = Field(default_factory=list)
    orientation: str = "vertical"
    text_threshold: int = Field(default=85, ge=0, le=100)

    @model_validator(mode="after")
    def populate_file_sources(self) -> GSTReconciliationRequest:
        if not self.file_1_id and self.source_files_1:
            self.file_1_id = self.source_files_1[0].file_id
        if not self.file_2_id and self.source_files_2:
            self.file_2_id = self.source_files_2[0].file_id
        if self.file_1_id and not self.source_files_1:
            self.source_files_1 = [FileSource(file_id=self.file_1_id)]
        if self.file_2_id and not self.source_files_2:
            self.source_files_2 = [FileSource(file_id=self.file_2_id)]
        return self


class AnalysisRequest(BaseModel):
    source_files_1: list[FileSource]
    source_files_2: list[FileSource]
    orientation: str = "vertical"


class PairAnalysisResult(BaseModel):
    sheet_id_1: str | None = None
    sheet_id_2: str | None = None
    recommended_keys_1: list[str]
    recommended_keys_2: list[str]
    key_confidence: int
    is_composite_key: bool
    recommended_mappings: list[dict]


class AnalysisResponse(BaseModel):
    recommended_keys_1: list[str]
    recommended_keys_2: list[str]
    key_confidence: int
    is_composite_key: bool
    recommended_mappings: list[dict]


class ReconciliationSummary(BaseModel):
    report_rows: int = 0
    only_in_file_1: int = 0
    only_in_file_2: int = 0
    confidence_review: int = 0
