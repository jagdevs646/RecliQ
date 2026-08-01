from pydantic import BaseModel, Field


class RuleMapping(BaseModel):
    file_1_fields: list[str] = Field(default_factory=list)
    file_2_fields: list[str] = Field(default_factory=list)


class GenericReconciliationRequest(BaseModel):
    file_1_id: str
    file_2_id: str
    key_file_1: str
    key_file_2: str
    rules: list[RuleMapping]
    orientation: str = "vertical"
    include_columns_file_1: list[str] = Field(default_factory=list)
    include_columns_file_2: list[str] = Field(default_factory=list)


class GSTReconciliationRequest(BaseModel):
    file_1_id: str
    file_2_id: str
    orientation: str = "vertical"
    text_threshold: int = Field(default=85, ge=0, le=100)


class ReconciliationSummary(BaseModel):
    report_rows: int = 0
    only_in_file_1: int = 0
    only_in_file_2: int = 0
    confidence_review: int = 0

