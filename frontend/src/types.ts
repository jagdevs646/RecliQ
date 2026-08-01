export type Page = "dashboard" | "upload" | "status" | "results" | "history";

export interface User {
  id: string;
  email: string;
  full_name: string;
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
