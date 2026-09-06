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

export interface AnalysisResponse {
  recommended_keys_1: string[];
  recommended_keys_2: string[];
  key_confidence: number;
  is_composite_key: boolean;
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
}

export type PreviewCategory = "discrepancies" | "only_file_1" | "only_file_2" | "review";

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
