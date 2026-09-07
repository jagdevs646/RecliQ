from pydantic import BaseModel, Field, model_validator


class RuleMapping(BaseModel):
    file_1_fields: list[str] = Field(default_factory=list)
    file_2_fields: list[str] = Field(default_factory=list)


class FileSource(BaseModel):
    file_id: str
    sheet_id: str | None = None


class SheetPairConfig(BaseModel):
    source_file_1: FileSource
    source_file_2: FileSource
    key_file_1: str | list[str]
    key_file_2: str | list[str]
    rules: list[RuleMapping]
    include_columns_file_1: list[str] = Field(default_factory=list)
    include_columns_file_2: list[str] = Field(default_factory=list)


class GenericReconciliationRequest(BaseModel):
    file_1_id: str | None = None  # Legacy and direct support
    file_2_id: str | None = None  # Legacy and direct support
    
    # Backwards compatibility
    source_files_1: list[FileSource] = Field(default_factory=list)
    source_files_2: list[FileSource] = Field(default_factory=list)
    key_file_1: str | list[str] | None = None
    key_file_2: str | list[str] | None = None
    rules: list[RuleMapping] = Field(default_factory=list)
    include_columns_file_1: list[str] = Field(default_factory=list)
    include_columns_file_2: list[str] = Field(default_factory=list)
    
    # New configuration
    pairs: list[SheetPairConfig] = Field(default_factory=list)
    
    orientation: str = "vertical"

    @model_validator(mode="after")
    def populate_file_sources(self) -> "GenericReconciliationRequest":
        # 1. Populate file_1_id / file_2_id from source_files if not set
        if not self.file_1_id and self.source_files_1:
            self.file_1_id = self.source_files_1[0].file_id
        if not self.file_2_id and self.source_files_2:
            self.file_2_id = self.source_files_2[0].file_id

        # 2. Populate source_files from file_1_id / file_2_id if not set
        if self.file_1_id and not self.source_files_1:
            self.source_files_1 = [FileSource(file_id=self.file_1_id)]
        if self.file_2_id and not self.source_files_2:
            self.source_files_2 = [FileSource(file_id=self.file_2_id)]

        # 3. Build pairs from legacy fields if not explicitly provided
        if not self.pairs and self.rules and self.key_file_1 and self.key_file_2:
            s1 = self.source_files_1[0] if self.source_files_1 else None
            s2 = self.source_files_2[0] if self.source_files_2 else None
            if s1 and s2:
                self.pairs = [
                    SheetPairConfig(
                        source_file_1=s1,
                        source_file_2=s2,
                        key_file_1=self.key_file_1,
                        key_file_2=self.key_file_2,
                        rules=self.rules,
                        include_columns_file_1=self.include_columns_file_1,
                        include_columns_file_2=self.include_columns_file_2,
                    )
                ]

        # 4. If pairs exist but file IDs are still missing, populate from pairs
        if not self.file_1_id and self.pairs:
            self.file_1_id = self.pairs[0].source_file_1.file_id
        if not self.file_2_id and self.pairs:
            self.file_2_id = self.pairs[0].source_file_2.file_id
            
        return self


class GSTReconciliationRequest(BaseModel):
    file_1_id: str | None = None
    file_2_id: str | None = None
    source_files_1: list[FileSource] = Field(default_factory=list)
    source_files_2: list[FileSource] = Field(default_factory=list)
    orientation: str = "vertical"
    text_threshold: int = Field(default=85, ge=0, le=100)

    @model_validator(mode="after")
    def populate_file_sources(self) -> "GSTReconciliationRequest":
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

