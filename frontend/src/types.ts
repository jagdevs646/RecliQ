export type Page = "dashboard" | "upload" | "status" | "results" | "history";

export interface User {
  id: string;
  email: string;
  full_name: string;
}

export interface SheetMetadata {
  id: string;
  name: string;
}

export interface FileMetadataResponse {
  file_id: string;
  filename: string;
  sheets: SheetMetadata[];
}

export interface FileSource {
  file_id: string;
  sheet_id?: string | null;
}

export interface UploadedFile {
  id: string;
  original_filename: string;
  content_type: string | null;
  size_bytes: number;
  storage_backend: string;
  created_at: string;
}

export interface RuleMapping {
  file_1_fields: string[];
  file_2_fields: string[];
}

export type SecondaryComparisonMethod = "exact_text" | "normalized_date" | "numeric_tolerance" | "matcher_based";

export interface SecondaryMatchCondition {
  source_column: string;
  destination_column: string;
  comparison_method: SecondaryComparisonMethod;
  numeric_tolerance?: number;
}

export interface SimilarityPolicy {
  matcher_type_override?: string;
  threshold?: number;
}

export interface SheetRuleDraft {
  file1Columns: string[];
  file2Columns: string[];
  primaryKeySource: string[];
  primaryKeyDestination: string[];
  secondaryConditions: SecondaryMatchCondition[];
  similarityPolicy: SimilarityPolicy;
  dateOnlyOverride: boolean;
  rules: RuleMapping[];
  includeFile1: string[];
  includeFile2: string[];
  analysis: AnalysisResponse | null;
}

export interface AnalysisResponse {
  recommended_keys_1: string[];
  recommended_keys_2: string[];
  key_confidence: number;
  is_composite_key: boolean;
  key_reason?: string;
  /** Columns whose values are dates (drives the date-only key guard). */
  date_columns_1?: string[];
  date_columns_2?: string[];
  recommended_mappings: {
    source: string;
    target: string | null;
    score: number;
    confidence: "High" | "Medium" | "Low" | "None";
  }[];
}

export interface GstConfiguration {
  required_columns: string[];
  matching_fields: string[];
  grouping_fields: string[];
  amount_fields: string[];
}

export interface Job {
  id: string;
  job_type: string;
  status: "queued" | "processing" | "completed" | "failed" | string;
  progress: number;
  orientation: string;
  error_message: string | null;
  input_file_1_id: string | null;
  input_file_2_id: string | null;
  input_file_1_name?: string | null;
  input_file_2_name?: string | null;
  file_pair_count?: number;
  report_id: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface ReconciliationSummary {
  report_rows: number;
  only_in_file_1: number;
  only_in_file_2: number;
  confidence_review: number;
  source_records?: number;
  destination_records?: number;
  matched_records?: number;
  fully_matched_records?: number;
  exact_matches?: number;
  exception_matches?: number;
  ambiguous_matches?: number;
  not_found_matches?: number;
  field_discrepancies?: number;
  completed_rules?: number;
  failed_rules?: number;
  /** Job manifest (generic jobs): one entry per file pair and per sheet rule. */
  file_pairs?: FilePairManifest[];
  sheet_rules?: SheetRuleManifest[];
}

export type RuleStatus = "completed" | "completed_with_errors" | "failed";

export interface FilePairManifest {
  file_pair_id: string;
  label: string;
  source_file: string | null;
  destination_file: string | null;
  status: RuleStatus;
  sheet_rule_ids: string[];
  summary: ReconciliationSummary;
  report_filename: string | null;
}

export interface SheetRuleManifest {
  file_pair_id: string;
  sheet_rule_id: string;
  rule_index: number;
  report_label: string;
  source_file: string | null;
  destination_file: string | null;
  source_sheets: string[];
  destination_sheets: string[];
  primary_key_source: string[];
  primary_key_destination: string[];
  secondary_conditions: SecondaryMatchCondition[];
  similarity_policy: SimilarityPolicy;
  date_only_override: boolean;
  mapping_count: number;
  status: RuleStatus;
  error: string | null;
  summary: ReconciliationSummary;
}

export interface ReportScope {
  filePairId?: string;
  sheetRuleId?: string;
}

export type PreviewCategory = "discrepancies" | "only_file_1" | "only_file_2" | "review" | "exception_matches" | "ambiguous_matches" | "not_found";

export interface ReportPreview {
  category: PreviewCategory;
  sheet_name: string;
  columns: string[];
  rows: Array<Record<string, string | number | boolean | null>>;
  total_rows: number;
  offset: number;
  limit: number;
}

export interface ReportCustomConfig {
  include_summary: boolean;
  include_exceptions: boolean;
  include_matched: boolean;
  include_missing_file_1: boolean;
  include_missing_file_2: boolean;
  include_field_differences: boolean;
  include_controls: boolean;
  date_format: string;
  number_format: string;
}
