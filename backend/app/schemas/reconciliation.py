from pydantic import BaseModel, Field


class RuleMapping(BaseModel):
    file_1_fields: list[str] = Field(default_factory=list)
    file_2_fields: list[str] = Field(default_factory=list)


class FileSource(BaseModel):
    file_id: str
    sheet_id: str | None = None


class GenericReconciliationRequest(BaseModel):
    file_1_id: str | None = None  # Legacy support
    file_2_id: str | None = None  # Legacy support
    source_files_1: list[FileSource] = Field(default_factory=list)
    source_files_2: list[FileSource] = Field(default_factory=list)
    key_file_1: str | list[str]
    key_file_2: str | list[str]
    rules: list[RuleMapping]
    orientation: str = "vertical"
    include_columns_file_1: list[str] = Field(default_factory=list)
    include_columns_file_2: list[str] = Field(default_factory=list)


class GSTReconciliationRequest(BaseModel):
    file_1_id: str | None = None
    file_2_id: str | None = None
    source_files_1: list[FileSource] = Field(default_factory=list)
    source_files_2: list[FileSource] = Field(default_factory=list)
    orientation: str = "vertical"
    text_threshold: int = Field(default=85, ge=0, le=100)


class AnalysisRequest(BaseModel):
    source_files_1: list[FileSource]
    source_files_2: list[FileSource]
    orientation: str = "vertical"


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

