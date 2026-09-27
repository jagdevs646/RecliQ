from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.reconciliation import GenericReconciliationRequest, SheetRuleConfig


class TemplateFilePair(BaseModel):
    """One file pair of a saved reconciliation, independent of any upload."""

    file_pair_id: str
    label: str = ""
    source_filename: str = ""
    destination_filename: str = ""
    sheet_rules: list[SheetRuleConfig] = Field(default_factory=list)
    report_metadata: dict[str, Any] = Field(default_factory=dict)


class TemplateConfig(BaseModel):
    schema_version: int = 1
    orientation: str = "vertical"
    file_pairs: list[TemplateFilePair] = Field(default_factory=list)
    report_settings: dict[str, Any] = Field(default_factory=dict)
    # Alternative header names per column (normalized header -> aliases), used
    # when a new file names a column differently.
    column_aliases: dict[str, list[str]] = Field(default_factory=dict)


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    # Exactly one source: a submitted plan, a finished job, or a config.
    plan: GenericReconciliationRequest | None = None
    job_id: str | None = None
    config: TemplateConfig | None = None
    report_settings: dict[str, Any] = Field(default_factory=dict)
    change_note: str = ""


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    plan: GenericReconciliationRequest | None = None
    config: TemplateConfig | None = None
    report_settings: dict[str, Any] | None = None
    column_aliases: dict[str, list[str]] | None = None
    change_note: str = ""


class TemplateFileAssignment(BaseModel):
    file_pair_id: str
    source_file_id: str
    destination_file_id: str


class TemplateRunRequest(BaseModel):
    files: list[TemplateFileAssignment]
    version: int | None = None
    # Manual corrections: {file_pair_id: {"source"|"destination": {template sheet: new sheet}}}
    sheet_overrides: dict[str, dict[str, dict[str, str]]] = Field(default_factory=dict)
    # {sheet_rule_id: {"source"|"destination": {template column: new column}}}
    column_overrides: dict[str, dict[str, dict[str, str]]] = Field(default_factory=dict)
    precheck_acknowledged: bool = False
    precheck_summary: dict[str, Any] = Field(default_factory=dict)
