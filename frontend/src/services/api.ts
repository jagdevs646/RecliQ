import type { AccountIdentity, AdminEmailDeliveryRecord, AdminEmailDeliverySummary, AdminOverview, AdminUser, AliasSuggestion, AnalysisResponse, AuditEvent, EntityAlias, FileMetadataResponse, FileSource, GenericPlanPayload, GstConfiguration, GstJobPlan, Job, JobPlan, LearningOverview, PrecheckResult, PrecheckTask, PreviewCategory, ReconciliationSummary, ReportEmailDelivery, ReportPreview, ReportScope, ResolutionRule, RuleCatalog, RuleDraft, RulePreviewResult, RuleSuggestion, SupportedFormats, TemplateResolution, TemplateRunRequest, TemplateSummary, UploadedFile, WorkspaceOverview, SupportTicket, SupportTicketMessage } from "../types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";
const SESSION_STORAGE_KEY = "recliq_session_id";

function readSessionId(): string | null {
  try {
    return localStorage.getItem(SESSION_STORAGE_KEY);
  } catch {
    return null;
  }
}

function rememberSession(response: Response): void {
  const sessionId = response.headers.get("X-Session-ID");
  if (!sessionId) return;
  try {
    localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
  } catch {
    // Cookies remain the server-side fallback when storage is unavailable.
  }
}

function rememberXhrSession(request: XMLHttpRequest): void {
  const sessionId = request.getResponseHeader("X-Session-ID");
  if (!sessionId) return;
  try {
    localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
  } catch {
    // Cookies remain the server-side fallback when storage is unavailable.
  }
}

/** Reads the server-chosen filename from Content-Disposition, else the fallback. */
function responseFilename(response: Response, fallback: string): string {
  const match = response.headers.get("Content-Disposition")?.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  return match ? decodeURIComponent(match[1]) : fallback;
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

/** An API error with its HTTP status and parsed body (e.g. a template resolution). */
export class ApiError extends Error {
  constructor(message: string, public status: number, public body: unknown) {
    super(message);
  }
}

function errorMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((item) => (item && typeof item === "object" && "msg" in item ? String((item as { msg: unknown }).msg) : String(item))).join("; ");
  }
  return fallback;
}

function sessionHeaders(): Headers {
  const headers = new Headers();
  const sessionId = readSessionId();
  if (sessionId) headers.set("X-Session-ID", sessionId);
  return headers;
}

export class ApiClient {
  private csrfToken: string | null = null;
  readonly baseUrl = API_BASE;

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers = new Headers(options.headers);
    if (!(options.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }

    const sessionId = readSessionId();
    if (sessionId) headers.set("X-Session-ID", sessionId);
    const method = (options.method ?? "GET").toUpperCase();
    if (!["GET", "HEAD", "OPTIONS", "TRACE"].includes(method) && this.csrfToken) {
      headers.set("X-CSRF-Token", this.csrfToken);
    }

    const response = await fetch(`${API_BASE}${path}`, { ...options, headers, credentials: "include" });
    rememberSession(response);
    this.rememberCsrf(response);
    if (!response.ok) {
      const text = await response.text();
      let body: unknown = text;
      try { body = JSON.parse(text); } catch { /* plain-text error */ }
      throw new ApiError(errorMessage(body, text || response.statusText), response.status, body);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return response.json() as Promise<T>;
  }

  private rememberCsrf(response: Response): void {
    const token = response.headers.get("X-CSRF-Token");
    if (token) this.csrfToken = token;
  }

  async uploadFile(file: File, onProgress?: (progress: number) => void): Promise<UploadedFile> {
    const formData = new FormData();
    formData.append("file", file);
    return new Promise<UploadedFile>((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("POST", `${API_BASE}/files/upload`);
      request.withCredentials = true;
      const sessionId = readSessionId();
      if (sessionId) request.setRequestHeader("X-Session-ID", sessionId);
      if (this.csrfToken) request.setRequestHeader("X-CSRF-Token", this.csrfToken);
      request.responseType = "json";
      request.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          onProgress?.(Math.round((event.loaded / event.total) * 100));
        }
      };
      request.onerror = () => reject(new Error("Upload failed. Check that the RecliQ API is running."));
      request.onload = () => {
        rememberXhrSession(request);
        const csrf = request.getResponseHeader("X-CSRF-Token");
        if (csrf) this.csrfToken = csrf;
        if (request.status >= 200 && request.status < 300) {
          resolve(request.response as UploadedFile);
          return;
        }
        const detail = request.response?.detail;
        reject(new Error(typeof detail === "string" ? detail : request.statusText || "Upload failed"));
      };
      request.send(formData);
    });
  }

  async getFileMetadata(fileId: string): Promise<FileMetadataResponse> {
    return this.request<FileMetadataResponse>(`/files/${fileId}/metadata`);
  }

  async getColumns(fileId: string, orientation: string, sheetId?: string | null): Promise<string[]> {
    const params = new URLSearchParams({ orientation });
    if (sheetId) params.append("sheet_id", sheetId);
    const result = await this.request<{ columns: string[] }>(`/files/${fileId}/columns?${params.toString()}`);
    return result.columns;
  }

  async analyzeFiles(payload: { source_files_1: FileSource[]; source_files_2: FileSource[]; orientation: string }): Promise<AnalysisResponse> {
    return this.request<AnalysisResponse>("/analysis/", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  }

  async startGeneric(payload: GenericPlanPayload): Promise<Job> {
    return this.request<Job>("/reconciliation/generic", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  }

  async getSupportedFormats(): Promise<SupportedFormats> {
    return this.request<SupportedFormats>("/files/formats");
  }

  /** Data-quality pre-check of a planned run (same body as the run request). */
  async precheck(payload: GenericPlanPayload): Promise<PrecheckResult> {
    let task = await this.request<PrecheckTask>("/analysis/precheck", { method: "POST", body: JSON.stringify(payload) });
    const deadline = Date.now() + 30 * 60 * 1000;
    while (true) {
      if (task.status === "completed" && task.result) return task.result;
      if (task.status === "failed") throw new Error(task.error || "The data check could not be completed.");
      if (Date.now() >= deadline) throw new Error("The data check is taking longer than expected. Try again in a moment.");
      await new Promise((resolve) => window.setTimeout(resolve, 500));
      task = await this.request<PrecheckTask>(`/analysis/precheck/${task.id}`);
    }
  }

  // ── Saved reconciliations ──────────────────────────────────────────────
  async listTemplates(): Promise<TemplateSummary[]> {
    return (await this.request<{ templates: TemplateSummary[] }>("/templates")).templates;
  }

  async getTemplate(templateId: string): Promise<TemplateSummary> {
    return this.request<TemplateSummary>(`/templates/${templateId}`);
  }

  async createTemplate(payload: { name: string; description?: string; plan?: GenericPlanPayload; job_id?: string }): Promise<TemplateSummary> {
    return this.request<TemplateSummary>("/templates", { method: "POST", body: JSON.stringify(payload) });
  }

  async updateTemplate(templateId: string, payload: { name?: string; description?: string; column_aliases?: Record<string, string[]>; change_note?: string }): Promise<TemplateSummary> {
    return this.request<TemplateSummary>(`/templates/${templateId}`, { method: "PUT", body: JSON.stringify(payload) });
  }

  async archiveTemplate(templateId: string): Promise<void> {
    await this.request(`/templates/${templateId}`, { method: "DELETE" });
  }

  /** Maps a template onto new files. A 422 carries the unresolved sheets/columns. */
  async resolveTemplate(templateId: string, payload: TemplateRunRequest): Promise<{ resolution: TemplateResolution; plan: GenericPlanPayload | null; error?: string }> {
    try {
      const result = await this.request<{ resolution: TemplateResolution; plan: GenericPlanPayload }>(`/templates/${templateId}/resolve`, { method: "POST", body: JSON.stringify(payload) });
      return result;
    } catch (error) {
      if (error instanceof ApiError && error.status === 422 && error.body && typeof error.body === "object" && "resolution" in error.body) {
        return { resolution: (error.body as { resolution: TemplateResolution }).resolution, plan: null, error: error.message };
      }
      throw error;
    }
  }

  async runTemplate(templateId: string, payload: TemplateRunRequest): Promise<Job> {
    return this.request<Job>(`/templates/${templateId}/run`, { method: "POST", body: JSON.stringify(payload) });
  }

  // ── Aliases and review decisions ───────────────────────────────────────
  async listAliases(): Promise<EntityAlias[]> {
    return (await this.request<{ aliases: EntityAlias[] }>("/aliases")).aliases;
  }

  async createAlias(canonical: string, variants: string[], columnHint = ""): Promise<EntityAlias[]> {
    return (await this.request<{ aliases: EntityAlias[] }>("/aliases", { method: "POST", body: JSON.stringify({ canonical, variants, column_hint: columnHint }) })).aliases;
  }

  async removeAlias(aliasId: string): Promise<void> {
    await this.request(`/aliases/${aliasId}`, { method: "DELETE" });
  }

  /** "reset" withdraws earlier decisions on the pair (e.g. a rejection made by mistake). */
  async recordDecision(payload: { value_1: string; value_2: string; decision: "accept" | "reject" | "reset"; job_id?: string; column_hint?: string; confidence?: number; note?: string; method?: string }): Promise<void> {
    await this.request("/aliases/decisions", { method: "POST", body: JSON.stringify(payload) });
  }

  // ── Auto-resolution rules and learning ────────────────────────────────
  async ruleCatalog(): Promise<RuleCatalog> {
    return this.request<RuleCatalog>("/resolution-rules/catalog");
  }

  async listRules(): Promise<ResolutionRule[]> {
    return (await this.request<{ rules: ResolutionRule[] }>("/resolution-rules")).rules;
  }

  async createRule(rule: RuleDraft): Promise<ResolutionRule> {
    return this.request<ResolutionRule>("/resolution-rules", { method: "POST", body: JSON.stringify(rule) });
  }

  async updateRule(ruleId: string, changes: Partial<RuleDraft>): Promise<ResolutionRule> {
    return this.request<ResolutionRule>(`/resolution-rules/${ruleId}`, { method: "PUT", body: JSON.stringify(changes) });
  }

  async archiveRule(ruleId: string): Promise<void> {
    await this.request(`/resolution-rules/${ruleId}`, { method: "DELETE" });
  }

  async reorderRules(ruleIds: string[]): Promise<ResolutionRule[]> {
    return (await this.request<{ rules: ResolutionRule[] }>("/resolution-rules/order", { method: "POST", body: JSON.stringify({ rule_ids: ruleIds }) })).rules;
  }

  async previewRule(jobId: string, rule: RuleDraft): Promise<RulePreviewResult> {
    return this.request<RulePreviewResult>("/resolution-rules/preview", { method: "POST", body: JSON.stringify({ job_id: jobId, rule }) });
  }

  async ruleSuggestions(): Promise<{ minimum_reconciliations: number; suggestions: RuleSuggestion[] }> {
    return this.request("/resolution-rules/suggestions");
  }

  async learningOverview(): Promise<LearningOverview> {
    return this.request<LearningOverview>("/learning");
  }

  async aliasSuggestions(): Promise<{ minimum_acceptances: number; suggestions: AliasSuggestion[] }> {
    return this.request("/aliases/suggestions");
  }

  async approveSuggestion(canonical: string, variant: string, columnHint = ""): Promise<EntityAlias> {
    return this.request<EntityAlias>("/aliases/suggestions/approve", { method: "POST", body: JSON.stringify({ canonical, variant, column_hint: columnHint }) });
  }

  async previewNormalization(value1: string, value2: string): Promise<{ equivalent: boolean; rules: string[]; value_1: { normalized: string }; value_2: { normalized: string } }> {
    return this.request("/aliases/preview", { method: "POST", body: JSON.stringify({ value_1: value1, value_2: value2 }) });
  }

  // ── Audit log ──────────────────────────────────────────────────────────
  async listAuditEvents(params: { action?: string; entity_id?: string; offset?: number; limit?: number } = {}): Promise<AuditEvent[]> {
    const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== undefined && value !== "").map(([key, value]) => [key, String(value)]));
    return (await this.request<{ events: AuditEvent[] }>(`/audit/events?${query.toString()}`)).events;
  }

  async verifyAuditLog(): Promise<{ valid: boolean; checked: number; broken_at: string | null; reason: string }> {
    return this.request("/audit/verify");
  }

  async downloadAuditLog(format: "csv" | "json"): Promise<void> {
    const response = await fetch(`${API_BASE}/audit/export?format=${format}`, { credentials: "include", headers: sessionHeaders() });
    rememberSession(response);
    if (!response.ok) throw new Error(await response.text());
    saveBlob(await response.blob(), responseFilename(response, `RecliQ_Audit_Log.${format}`));
  }

  async startGst(payload: {
    file_1_id?: string;
    file_2_id?: string;
    source_files_1?: FileSource[];
    source_files_2?: FileSource[];
    orientation: string;
    text_threshold: number;
  }): Promise<Job> {
    return this.request<Job>("/reconciliation/gst", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  }

  async getGstConfiguration(): Promise<GstConfiguration> {
    return this.request<GstConfiguration>("/reconciliation/gst/config");
  }

  async initializeSession(): Promise<string> {
    const result = await this.request<{ session_id: string }>("/session");
    try {
      localStorage.setItem(SESSION_STORAGE_KEY, result.session_id);
    } catch {
      // The HttpOnly cookie still identifies this browser session.
    }
    return result.session_id;
  }

  async identity(): Promise<AccountIdentity> {
    return this.request<AccountIdentity>("/auth/me");
  }

  async register(payload: { username: string; email: string; display_name: string; password: string }): Promise<{ message: string; email: string; approval_required?: boolean; development_verification_url?: string }> {
    return this.request("/auth/register", { method: "POST", body: JSON.stringify(payload) });
  }

  async login(payload: { identifier: string; password: string; totp_code?: string }): Promise<AccountIdentity> {
    await this.request("/auth/login", { method: "POST", body: JSON.stringify(payload) });
    return this.identity();
  }

  async logout(): Promise<void> {
    await this.request<void>("/auth/logout", { method: "POST" });
    this.csrfToken = null;
  }

  async updateProfile(display_name: string): Promise<AccountIdentity> {
    await this.request("/auth/profile", { method: "PUT", body: JSON.stringify({ display_name }) });
    return this.identity();
  }

  async changePassword(payload: { current_password: string; new_password: string }): Promise<{ message: string }> {
    return this.request("/auth/password/change", { method: "POST", body: JSON.stringify(payload) });
  }

  async requestPasswordReset(email: string): Promise<{ message: string; development_reset_url?: string }> {
    return this.request("/auth/password/forgot", { method: "POST", body: JSON.stringify({ email }) });
  }

  async resetPassword(token: string, new_password: string): Promise<{ message: string }> {
    return this.request("/auth/password/reset", { method: "POST", body: JSON.stringify({ token, new_password }) });
  }

  async verifyEmail(token: string): Promise<{ message: string }> {
    return this.request("/auth/verify-email", { method: "POST", body: JSON.stringify({ token }) });
  }

  async resendVerification(email: string): Promise<{ message: string; development_verification_url?: string }> {
    return this.request("/auth/verification/resend", { method: "POST", body: JSON.stringify({ email }) });
  }

  async verifyEmailChange(token: string): Promise<{ message: string }> {
    return this.request("/auth/email/change/verify", { method: "POST", body: JSON.stringify({ token }) });
  }

  async oauthProviders(): Promise<Array<{ id: string; label: string; enabled: boolean }>> {
    return (await this.request<{ providers: Array<{ id: string; label: string; enabled: boolean }> }>("/auth/oauth/providers")).providers;
  }

  oauthStartUrl(provider: string, mode: "login" | "link" = "login"): string {
    return `${API_BASE}/auth/oauth/${encodeURIComponent(provider)}?mode=${mode}`;
  }

  async deleteAccount(confirm: "DELETE", current_password?: string): Promise<void> {
    await this.request<void>("/auth/account/delete", { method: "POST", body: JSON.stringify({ confirm, current_password }) });
    this.csrfToken = null;
  }

  async importGuestWorkspace(confirm: boolean): Promise<{ message: string; counts: Record<string, number> }> {
    return this.request("/auth/import-guest", { method: "POST", body: JSON.stringify({ confirm }) });
  }

  async listSessions(): Promise<Array<{ id: string; created_at: string; last_seen_at: string; expires_at: string; user_agent: string | null; current: boolean }>> {
    return (await this.request<{ sessions: Array<{ id: string; created_at: string; last_seen_at: string; expires_at: string; user_agent: string | null; current: boolean }> }>("/auth/sessions")).sessions;
  }

  async revokeSession(id: string): Promise<void> {
    await this.request<void>(`/auth/sessions/${id}`, { method: "DELETE" });
  }

  async preferences(): Promise<{ theme: "light" | "dark" | "system"; notifications: Record<string, boolean> }> {
    return this.request("/auth/preferences");
  }

  async updatePreferences(payload: { theme: "light" | "dark" | "system"; notifications?: Record<string, boolean> }): Promise<{ theme: "light" | "dark" | "system"; notifications: Record<string, boolean> }> {
    return this.request("/auth/preferences", { method: "PUT", body: JSON.stringify(payload) });
  }

  async setupMfa(): Promise<{ secret: string; otpauth_url: string }> {
    return this.request("/auth/mfa/setup", { method: "POST" });
  }

  async enableMfa(code: string): Promise<{ message: string }> {
    return this.request("/auth/mfa/enable", { method: "POST", body: JSON.stringify({ code }) });
  }

  async changeEmail(new_email: string, current_password: string): Promise<{ message: string }> {
    return this.request("/auth/email/change", { method: "POST", body: JSON.stringify({ new_email, current_password }) });
  }

  async workspaceOverview(period: "7d" | "30d" | "all"): Promise<WorkspaceOverview> {
    return this.request(`/workspace/overview?period=${period}`);
  }

  async adminOverview(period: "today" | "7d" | "30d" | "all" | "custom", dateFrom?: string, dateTo?: string): Promise<AdminOverview> {
    const query = new URLSearchParams({ period });
    if (dateFrom) query.set("date_from", dateFrom);
    if (dateTo) query.set("date_to", dateTo);
    return this.request(`/admin/overview?${query.toString()}`);
  }

  async adminUsers(params: { q?: string; status?: string; role?: string; sort?: string; created_from?: string; created_to?: string; active_from?: string; active_to?: string; runs_min?: number | string; runs_max?: number | string; storage_min?: number; storage_max?: number; page?: number; page_size?: number } = {}): Promise<{ users: AdminUser[]; total: number; page: number; page_size: number }> {
    const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== undefined).map(([key, value]) => [key, String(value)]));
    return this.request(`/admin/users?${query.toString()}`);
  }

  async adminPolicy(): Promise<{ features: Record<string, boolean>; limits: Record<string, Record<string, number | string[]>>; available_formats: Array<{ extension: string; label: string; content_type: string }>; email_ready: boolean }> {
    return this.request("/admin/policy");
  }

  async adminEmailSettings(): Promise<{ sender_email: string; transport: "smtp" | "local_log" | "disabled"; ready: boolean; smtp_ready: boolean; smtp_host_configured: boolean; smtp_auth_configured: boolean; local_only: boolean }> {
    return this.request("/admin/email-settings");
  }

  async updateAdminEmailSettings(sender_email: string): Promise<{ sender_email: string; transport: "smtp" | "local_log" | "disabled"; ready: boolean; smtp_ready: boolean; smtp_host_configured: boolean; smtp_auth_configured: boolean; local_only: boolean }> {
    return this.request("/admin/email-settings", { method: "PUT", body: JSON.stringify({ sender_email }) });
  }

  async sendAdminTestEmail(): Promise<{ message: string; transport: "smtp" | "local_log" }> {
    return this.request("/admin/email-settings/test", { method: "POST", body: "{}" });
  }

  async updateAdminPolicy(payload: { features?: Record<string, boolean>; limits?: Record<string, Record<string, number | string[]>> }): Promise<unknown> {
    return this.request("/admin/policy", { method: "PUT", body: JSON.stringify(payload) });
  }

  async updateAdminUserStatus(id: string, status: "active" | "restricted" | "suspended", reason: string): Promise<unknown> {
    return this.request(`/admin/users/${id}/status`, { method: "PUT", body: JSON.stringify({ status, reason }) });
  }

  async adminApproveUserVerification(id: string, reason: string): Promise<{ message: string }> {
    return this.request(`/admin/users/${id}/approve-verification`, { method: "POST", body: JSON.stringify({ reason }) });
  }

  async updateAdminUserPolicy(id: string, payload: { features: Record<string, boolean>; limits: Record<string, number | string[]> }): Promise<unknown> {
    return this.request(`/admin/users/${id}/policy`, { method: "PUT", body: JSON.stringify(payload) });
  }

  async adminRequirePasswordReset(id: string, reason: string): Promise<{ message: string }> {
    return this.request(`/admin/users/${id}/require-password-reset`, { method: "POST", body: JSON.stringify({ reason }) });
  }

  async adminDeleteUser(id: string, reason: string): Promise<{ message: string }> {
    return this.request(`/admin/users/${id}/delete`, { method: "POST", body: JSON.stringify({ reason }) });
  }

  async adminAudit(params: { action?: string; target_id?: string; offset?: number; limit?: number } = {}): Promise<unknown> {
    const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== undefined && value !== "").map(([key, value]) => [key, String(value)]));
    return this.request(`/admin/audit?${query.toString()}`);
  }

  async adminVerifyAudit(): Promise<{ valid: boolean; checked: number; broken_at: string | null }> {
    return this.request("/admin/audit/verify");
  }

  async jobList(params: { q?: string; status?: string; date_from?: string; date_to?: string; sort?: string; page?: number; page_size?: number } = {}): Promise<{ jobs: Job[]; total: number; page: number; page_size: number }> {
    const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== undefined && value !== "").map(([key, value]) => [key, String(value)]));
    return this.request(`/jobs?${query.toString()}`);
  }

  async renameJob(jobId: string, display_name: string): Promise<Job> {
    return this.request(`/jobs/${jobId}/name`, { method: "PUT", body: JSON.stringify({ display_name }) });
  }

  async sendReportEmail(jobId: string, payload: { recipients: string[]; subject: string; message?: string; include_summary: boolean; delivery_method: "attachment" | "secure_link"; idempotency_key: string }): Promise<ReportEmailDelivery[]> {
    return this.request(`/reports/job/${jobId}/email`, { method: "POST", body: JSON.stringify(payload) });
  }

  async listReportEmails(jobId: string): Promise<ReportEmailDelivery[]> {
    return (await this.request<{ deliveries: ReportEmailDelivery[] }>(`/reports/job/${jobId}/email`)).deliveries;
  }

  async revokeReportLink(deliveryId: string): Promise<void> {
    await this.request(`/reports/email-deliveries/${deliveryId}/link`, { method: "DELETE" });
  }

  async retryReportEmail(deliveryId: string): Promise<ReportEmailDelivery> {
    return this.request(`/reports/email-deliveries/${deliveryId}/retry`, { method: "POST" });
  }

  async adminUserDetail(id: string): Promise<unknown> {
    return this.request(`/admin/users/${id}`);
  }

  async adminRevokeSessions(id: string, reason: string): Promise<{ revoked_count: number }> {
    return this.request(`/admin/users/${id}/revoke-sessions`, { method: "POST", body: JSON.stringify({ reason }) });
  }

  async adminEmailDeliverySummary(): Promise<AdminEmailDeliverySummary> {
    return this.request("/admin/email-deliveries/summary");
  }

  async adminEmailDeliveries(params: { status?: string; offset?: number; limit?: number } = {}): Promise<{ deliveries: AdminEmailDeliveryRecord[]; total: number; offset: number; limit: number }> {
    const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== undefined && value !== "").map(([key, value]) => [key, String(value)]));
    return this.request(`/admin/email-deliveries?${query.toString()}`);
  }

  async adminHealth(): Promise<unknown> {
    return this.request("/admin/health");
  }

  async adminProductInsights(period: "today" | "7d" | "30d" | "all" = "all"): Promise<Record<string, unknown>> {
    return this.request(`/admin/product-insights?period=${period}`);
  }

  async adminComplaintSummary(): Promise<{ total: number; unresolved: number; unread: number; new: number; urgent: number; high: number; by_category: Record<string, number>; by_priority: Record<string, number> }> {
    return this.request("/admin/complaints/summary");
  }

  async adminComplaints(params: { category?: string; status?: string; priority?: string; user_id?: string; q?: string; page?: number; page_size?: number } = {}): Promise<{ tickets: SupportTicket[]; total: number; page: number; page_size: number }> {
    const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== undefined && value !== "").map(([key, value]) => [key, String(value)]));
    return this.request(`/admin/complaints?${query.toString()}`);
  }

  async adminComplaint(ticketId: string): Promise<SupportTicket> {
    return this.request(`/admin/complaints/${ticketId}`);
  }

  async updateAdminComplaint(ticketId: string, payload: { status?: SupportTicket["status"]; priority?: SupportTicket["priority"]; reply?: string }): Promise<SupportTicket> {
    return this.request(`/admin/complaints/${ticketId}`, { method: "PATCH", body: JSON.stringify(payload) });
  }

  async markAdminComplaintsRead(): Promise<{ marked_read: number }> {
    return this.request("/admin/complaints/mark-read", { method: "POST" });
  }

  async platformActivity(params: { q?: string; action?: string; source?: "all" | "workspace" | "admin"; date_from?: string; date_to?: string; offset?: number; limit?: number } = {}): Promise<{ events: Array<Record<string, unknown>>; total: number; offset: number; limit: number }> {
    const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== undefined && value !== "").map(([key, value]) => [key, String(value)]));
    return this.request(`/admin/activity?${query.toString()}`);
  }

  async listSupportTickets(page = 1, pageSize = 20): Promise<{ tickets: SupportTicket[]; total: number; page: number; page_size: number }> {
    return this.request(`/support?page=${page}&page_size=${pageSize}`);
  }

  async createSupportTicket(payload: Pick<SupportTicket, "category" | "custom_area" | "workflow_stage" | "impact" | "subject" | "description" | "expected_behavior" | "reproduction_steps">): Promise<SupportTicket> {
    return this.request("/support", { method: "POST", body: JSON.stringify(payload) });
  }

  async supportTicket(ticketId: string): Promise<SupportTicket> {
    return this.request(`/support/${ticketId}`);
  }

  async replyToSupportTicket(ticketId: string, body: string): Promise<SupportTicketMessage> {
    return this.request(`/support/${ticketId}/messages`, { method: "POST", body: JSON.stringify({ body }) });
  }

  async listJobs(): Promise<Job[]> {
    const result = await this.request<{ jobs: Job[] }>("/jobs");
    return result.jobs;
  }

  async getJob(jobId: string): Promise<Job> {
    return this.request<Job>(`/jobs/${jobId}`);
  }

  async getJobPlan(jobId: string): Promise<JobPlan> {
    return this.request<JobPlan>(`/jobs/${jobId}/plan`);
  }

  async getGstJobPlan(jobId: string): Promise<GstJobPlan> {
    return this.request<GstJobPlan>(`/jobs/${jobId}/gst-plan`);
  }

  async cancelJob(jobId: string): Promise<Job> {
    return this.request<Job>(`/jobs/${jobId}/cancel`, { method: "POST" });
  }

  async deleteJob(jobId: string): Promise<void> {
    await this.request<void>(`/jobs/${jobId}`, { method: "DELETE" });
  }

  async clearHistory(): Promise<{ deleted_count: number }> {
    return this.request<{ deleted_count: number }>("/jobs", { method: "DELETE" });
  }

  async getReportSummary(jobId: string): Promise<ReconciliationSummary> {
    return this.request<ReconciliationSummary>(`/reports/job/${jobId}/summary`);
  }

  async getReportPreview(
    jobId: string,
    category: PreviewCategory,
    offset = 0,
    scope: ReportScope = {},
    options: { search?: string; sortBy?: string; sortDirection?: "asc" | "desc"; signal?: AbortSignal } = {},
  ): Promise<ReportPreview> {
    const params = new URLSearchParams({ category, offset: String(offset), limit: "25" });
    if (scope.filePairId) params.append("file_pair_id", scope.filePairId);
    if (scope.sheetRuleId) params.append("sheet_rule_id", scope.sheetRuleId);
    if (options.search?.trim()) params.append("search", options.search.trim());
    if (options.sortBy) params.append("sort_by", options.sortBy);
    if (options.sortDirection) params.append("sort_direction", options.sortDirection);
    return this.request<ReportPreview>(`/reports/job/${jobId}/preview?${params.toString()}`, { signal: options.signal });
  }

  reportUrl(jobId: string, filePairId?: string): string {
    const query = filePairId ? `?file_pair_id=${encodeURIComponent(filePairId)}` : "";
    return `${API_BASE}/reports/job/${jobId}/download${query}`;
  }

  /** Downloads the job report: one workbook, a ZIP of every file pair, or one pair from that ZIP. */
  async downloadJobReport(jobId: string, filePairId?: string): Promise<void> {
    const response = await fetch(this.reportUrl(jobId, filePairId), { credentials: "include", headers: sessionHeaders() });
    rememberSession(response);
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const isZip = (response.headers.get("Content-Type") ?? "").includes("zip");
    saveBlob(await response.blob(), responseFilename(response, isZip ? "RecliQ_Reconciliation_Reports.zip" : "RecliQ_Reconciliation_Report.xlsx"));
  }

  async downloadCustomReport(jobId: string, config: import("../types").ReportCustomConfig, filePairId?: string): Promise<void> {
    const query = filePairId ? `?file_pair_id=${encodeURIComponent(filePairId)}` : "";
    const response = await fetch(`${API_BASE}/reports/job/${jobId}/download_custom${query}`, {
      method: "POST",
      body: JSON.stringify(config),
      credentials: "include",
      headers: new Headers({
        ...Object.fromEntries(sessionHeaders().entries()),
        ...(this.csrfToken ? { "X-CSRF-Token": this.csrfToken } : {}),
        "Content-Type": "application/json"
      })
    });
    rememberSession(response);
    this.rememberCsrf(response);
    if (!response.ok) {
      throw new Error(await response.text());
    }
    saveBlob(await response.blob(), responseFilename(response, "RecliQ_Custom_Report.xlsx"));
  }

  async downloadSampleTemplate(type: "generic" | "gst"): Promise<void> {
    const response = await fetch(`${API_BASE}/reconciliation/sample-template?type=${type}`, {
      credentials: "include",
      headers: sessionHeaders(),
    });
    rememberSession(response);
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = type === "gst" ? "RecliQ_GST_Sample_Template.xlsx" : "RecliQ_General_Sample_Template.xlsx";
    link.click();
    URL.revokeObjectURL(url);
  }
}

export const api = new ApiClient();
