export type Page = "dashboard" | "upload" | "status" | "results" | "history" | "saved" | "aliases" | "audit" | "rules" | "learning" | "settings" | "support" | "admin";
export type AdminSection = "dashboard" | "users" | "features" | "limits" | "insights" | "complaints" | "activity" | "email" | "preview";

export type ThemeMode = "light" | "dark" | "system";

export interface AccountIdentity {
  authenticated: boolean;
  user: null | {
    id: string;
    username: string;
    email: string;
    display_name: string;
    role: "user" | "super_admin";
    status: "active" | "restricted" | "suspended" | "deleted";
    email_verified: boolean;
    mfa_enabled: boolean;
    has_password: boolean;
    theme?: ThemeMode;
  };
  workspace: { type: "guest" | "account"; id: string | null; label?: string };
  features?: Record<string, boolean>;
  capabilities?: { report_email: boolean; email_notifications: boolean; registration?: boolean };
}

export interface WorkspaceOverview {
  workspace: { type: "guest" | "account"; label: string; user: { id: string; username: string; display_name: string; role: string } | null };
  period: "7d" | "30d" | "all";
  metrics: { runs: number; matched_records: number; exceptions_flagged: number; awaiting_review: number; completed_runs: number; failed_runs: number; active_runs: number };
  definitions: Record<string, string>;
  activity: Array<{ date: string; runs: number }>;
  attention: Job[];
  recent_jobs: Job[];
  saved_setups: Array<{ id: string; name: string; description: string; last_run_at: string | null; current_version: number }>;
  usage: { storage_bytes: number; jobs_today: number; jobs_month: number; active_jobs: number; storage_limit_bytes: number; jobs_per_day_limit: number; jobs_per_month_limit: number; concurrent_jobs_limit: number };
  features: Record<string, boolean>;
}

export interface AdminUser {
  id: string; username: string; email: string; display_name: string; role: string; status: string;
  email_verified: boolean; created_at: string; last_seen_at: string | null;
  usage: { reconciliation_count: number; file_count: number; uploaded_bytes: number; report_count: number; report_bytes: number; storage_bytes: number; jobs_by_status: Record<string, number> };
}

export interface AdminOverview {
  period: string;
  accounts: { total: number; active: number; suspended: number; restricted: number; new_in_period: number; active_in_period: number };
  jobs: { total: number; completed: number; failed: number; queued_or_processing: number; registered: number; guest: number };
  files: { count: number; bytes: number };
  reports: { count: number; bytes: number };
  emails: Record<string, number>;
  complaints: { total: number; unresolved: number; unread: number; new: number; urgent: number; high: number; by_priority: Record<string, number>; by_category: Record<string, number> };
  activity: { jobs: Array<{ date: string; count: number }>; accounts: Array<{ date: string; count: number }> };
  recent_admin_activity: Array<{ id: string; occurred_at: string; actor_id: string; action: string; target_type: string; target_id: string | null; result: string }>;
  period_start: string | null;
}

export interface SupportTicket {
  id: string;
  category: string;
  custom_area: string;
  workflow_stage: string;
  impact: string;
  subject: string;
  description: string;
  expected_behavior: string;
  reproduction_steps: string;
  status: "new" | "in_progress" | "waiting_for_user" | "resolved";
  priority: "low" | "normal" | "high" | "urgent";
  requester_type: "guest" | "account";
  requester: { user_id: string | null; username: string | null; display_name: string; email: string | null; scope_id: string };
  created_at: string;
  updated_at: string;
  admin_read_at: string | null;
  messages?: SupportTicketMessage[];
}

export interface SupportTicketMessage {
  id: string;
  author_type: "user" | "admin";
  author_label: string;
  body: string;
  created_at: string;
}

export interface ReportEmailDelivery {
  id: string;
  job_id: string;
  report_id: string;
  recipients: string[];
  subject: string;
  delivery_method: "attachment" | "secure_link";
  status: "queued" | "processing" | "sent" | "failed";
  transport?: "smtp" | "local_log" | null;
  error_message: string | null;
  created_at: string;
  sent_at: string | null;
}

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

export type TransformationOperation =
  | "trim" | "uppercase" | "lowercase" | "remove_characters" | "replace_text" | "remove_prefix" | "remove_suffix"
  | "strip_leading_zeros" | "keep_alphanumeric" | "invert_sign" | "absolute_value" | "multiply" | "round"
  | "debit_credit_to_signed";

export interface TransformationStep {
  operation: TransformationOperation;
  side: "source" | "destination" | "both";
  columns: string[];
  params: Record<string, string | number | boolean>;
  output_column?: string | null;
}

export interface MatchingPass {
  type: "amount_date" | "amount_tolerance";
  name?: string;
  enabled?: boolean;
  amount_source: string;
  amount_destination: string;
  date_source?: string | null;
  date_destination?: string | null;
  date_window_days: number;
  amount_tolerance: number;
  amount_tolerance_percent: number;
  narrative_source?: string | null;
  narrative_destination?: string | null;
  narrative_threshold: number;
  respect_secondary_keys: boolean;
}

/** Differences small enough to accept, applied after matching. */
export interface ToleranceBand {
  /** A compared field (its source column(s), as mapped), or "*" for every compared field. */
  field: string;
  amount?: number | null;
  percent?: number | null;
  days?: number | null;
  /** Text: accepted when at least this % similar (the report's Similarity). */
  similarity?: number | null;
}

export interface NormalizationSettings {
  legal_forms: boolean;
  abbreviations: boolean;
  ignore_prefixes: boolean;
  join_initials: boolean;
  word_order: boolean;
  use_saved_aliases: boolean;
  synonyms: Array<{ term: string; replacement: string }>;
  aliases: Array<{ canonical: string; variants: string[] }>;
}

export type DateFormat = "day_first" | "month_first";

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
  transformations: TransformationStep[];
  matchingPasses: MatchingPass[];
  normalization: NormalizationSettings;
  dateFormat: DateFormat;
  tolerances: ToleranceBand[];
}

/** One sheet rule as sent to the API (canonical plan). */
export interface SheetRulePayload {
  sheet_rule_id: string;
  source_sheets: string[];
  destination_sheets: string[];
  matching_strategy: {
    primary_key_source: string[];
    primary_key_destination: string[];
    secondary_conditions: SecondaryMatchCondition[];
    similarity_policy: SimilarityPolicy;
    date_only_override: boolean;
    matching_passes: MatchingPass[];
    normalization: NormalizationSettings;
  };
  reconciliation_mapping: RuleMapping[];
  include_columns_file_1: string[];
  include_columns_file_2: string[];
  transformations: TransformationStep[];
  date_format: DateFormat;
  tolerances: ToleranceBand[];
  report_label: string;
}

export interface FilePairPayload {
  file_pair_id: string;
  source_files: FileSource[];
  destination_files: FileSource[];
  report_metadata?: { label?: string };
  sheet_rules: SheetRulePayload[];
}

/** A finished run's files and rules, reopened to change them and run again. */
export interface JobPlan {
  job_id: string;
  orientation: string;
  file_pairs: FilePairPayload[];
  files: UploadedFile[];
}

export interface GstJobPlan {
  job_id: string;
  orientation: string;
  source_files_1: FileSource[];
  source_files_2: FileSource[];
  text_threshold: number;
  files: UploadedFile[];
}

export interface GenericPlanPayload {
  orientation: string;
  file_pairs: FilePairPayload[];
  file_1_id?: string;
  file_2_id?: string;
  precheck_acknowledged?: boolean;
  precheck_summary?: Record<string, unknown>;
}

export type PrecheckSeverity = "blocker" | "warning" | "info";

export interface PrecheckIssue {
  severity: PrecheckSeverity;
  category: string;
  side: "source" | "destination" | null;
  column: string | null;
  message: string;
  count: number | null;
  examples: Array<{ row: number | null; value: string | null }>;
  suggestion: { date_format?: DateFormat } | null;
}

export interface PrecheckRuleResult {
  file_pair_id: string;
  sheet_rule_id: string;
  label: string;
  date_format: DateFormat;
  source_rows?: number;
  destination_rows?: number;
  issues: PrecheckIssue[];
}

export interface PrecheckResult {
  status: "ok" | "warnings" | "blockers";
  summary: Record<PrecheckSeverity, number>;
  rules: PrecheckRuleResult[];
}

export interface TemplateSummary {
  id: string;
  name: string;
  description: string;
  current_version: number;
  archived: boolean;
  created_at: string | null;
  updated_at: string | null;
  last_run_at: string | null;
  summary: {
    file_pairs: number;
    sheet_rules: number;
    keys: string[];
    pairs: Array<{ file_pair_id: string; label: string; source_filename: string; destination_filename: string; sheet_rules: number }>;
  };
  config?: { file_pairs: Array<{ file_pair_id: string; label: string; sheet_rules: SheetRulePayload[] }>; column_aliases: Record<string, string[]>; report_settings: Record<string, unknown> };
  versions?: Array<{ version: number; change_note: string; created_at: string | null; created_by: string }>;
}

export interface TemplateResolutionItem {
  side?: "source" | "destination";
  template: string | null;
  resolved: string | null;
  method: string;
}

export interface TemplateResolution {
  file_pairs: Array<{ file_pair_id: string; label: string; sheets: TemplateResolutionItem[]; rules: Array<{ sheet_rule_id: string; columns: TemplateResolutionItem[] }> }>;
  unresolved: Array<{ file_pair_id: string; sheet_rule_id?: string; side?: "source" | "destination"; sheet?: string; column?: string; problem: string; available?: string[] }>;
  automatic?: TemplateResolutionItem[];
}

export interface TemplateRunRequest {
  files: Array<{ file_pair_id: string; source_file_id: string; destination_file_id: string }>;
  sheet_overrides?: Record<string, Record<string, Record<string, string>>>;
  column_overrides?: Record<string, Record<string, Record<string, string>>>;
  precheck_acknowledged?: boolean;
  precheck_summary?: Record<string, unknown>;
}

export interface AuditEvent {
  sequence: number;
  event_id: string;
  occurred_at: string;
  actor_type: string;
  actor_id: string;
  ip_address: string | null;
  request_id: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  summary: string;
  before: unknown;
  after: unknown;
  metadata: Record<string, unknown> | null;
}

export interface EntityAlias {
  id: string;
  canonical: string;
  variant: string;
  column_hint: string;
  active: boolean;
  source: string;
  created_at: string | null;
}

export interface AliasSuggestion {
  pair_key: string;
  value_1: string;
  value_2: string;
  acceptances: number;
  jobs: number;
  column_hint: string;
}

export interface SupportedFormats {
  formats: Array<{ extension: string; label: string; content_type: string }>;
  max_upload_mb: number;
  max_rows_per_sheet: number;
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
  display_name?: string | null;
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
  attempts?: number;
  template_id?: string | null;
  template_version?: number | null;
  matched_records_count?: number;
  exception_count?: number;
  review_count?: number;
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
  /** Records whose only differences were within the rule's tolerance. */
  within_tolerance_records?: number;
  within_tolerance_fields?: number;
  exact_matches?: number;
  exception_matches?: number;
  ambiguous_matches?: number;
  not_found_matches?: number;
  field_discrepancies?: number;
  completed_rules?: number;
  failed_rules?: number;
  keyless_matches?: number;
  normalized_matches?: number;
  /** Exceptions explained by auto-resolution rules (see the Auto-resolved tab). */
  auto_resolved?: number;
  auto_resolved_only_in_file_1?: number;
  auto_resolved_only_in_file_2?: number;
  auto_resolved_differences?: number;
  auto_confirmed_matches?: number;
  resolution_rules?: Array<{ id: string; name: string; version: number }>;
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

export type PreviewCategory = "discrepancies" | "only_file_1" | "only_file_2" | "review" | "exception_matches" | "ambiguous_matches" | "not_found" | "auto_resolved";

// ── Auto-resolution rules and learning ──────────────────────────────────
export type RuleCategory = "only_in_source" | "only_in_destination" | "field_difference" | "to_confirm";

export interface RuleCondition {
  operator: string;
  column?: string;
  value?: string | number | null;
  value_2?: string | number | null;
}

export interface RuleAction {
  resolution: string;
  reason_code?: string;
  gl_account?: string;
  note?: string;
}

export interface RuleDraft {
  name: string;
  description?: string;
  category: RuleCategory;
  conditions: RuleCondition[];
  action: RuleAction;
  enabled?: boolean;
}

export interface ResolutionRule extends RuleDraft {
  id: string;
  version: number;
  priority: number;
  enabled: boolean;
  archived: boolean;
  summary: string;
  category_label: string;
  updated_at: string | null;
}

export interface RuleCatalog {
  categories: Array<{ id: RuleCategory; label: string }>;
  operators: Array<{ id: string; label: string; group: "text" | "number" | "difference" | "confirmation"; categories: RuleCategory[] }>;
}

export interface RuleSuggestion {
  kind: "recurring_exception" | "auto_confirm_method" | "auto_confirm_repeat_pairs" | "weak_method";
  title: string;
  detail: string;
  evidence: Record<string, unknown>;
  rule: RuleDraft | null;
}

export interface RulePreviewResult {
  matches: number;
  summary: string;
  sample: Array<Record<string, string | number | boolean | null>>;
}

export interface MethodWeight {
  method: string;
  band: string;
  accepted: number;
  rejected: number;
  total: number;
  acceptance_rate: number;
  lower_bound: number;
  enough_evidence: boolean;
}

export interface LearningOverview {
  minimum_evidence: number;
  method_weights: MethodWeight[];
  remembered_rejections: Array<{ value_1: string; value_2: string; method: string | null; job_id: string | null; rejected_on: string }>;
  suggestions: RuleSuggestion[];
  alias_suggestions: number;
  confirmed_pairs: number;
}

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
  include_auto_resolved?: boolean;
  date_format: string;
  number_format: string;
}
