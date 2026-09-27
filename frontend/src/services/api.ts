import type { AliasSuggestion, AnalysisResponse, AuditEvent, EntityAlias, FileMetadataResponse, FileSource, GenericPlanPayload, GstConfiguration, Job, PrecheckResult, PreviewCategory, ReconciliationSummary, ReportPreview, ReportScope, SupportedFormats, TemplateResolution, TemplateRunRequest, TemplateSummary, UploadedFile } from "../types";

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
  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers = new Headers(options.headers);
    if (!(options.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }

    const sessionId = readSessionId();
    if (sessionId) headers.set("X-Session-ID", sessionId);

    const response = await fetch(`${API_BASE}${path}`, { ...options, headers, credentials: "include" });
    rememberSession(response);
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

  async uploadFile(file: File, onProgress?: (progress: number) => void): Promise<UploadedFile> {
    const formData = new FormData();
    formData.append("file", file);
    return new Promise<UploadedFile>((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("POST", `${API_BASE}/files/upload`);
      request.withCredentials = true;
      const sessionId = readSessionId();
      if (sessionId) request.setRequestHeader("X-Session-ID", sessionId);
      request.responseType = "json";
      request.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          onProgress?.(Math.round((event.loaded / event.total) * 100));
        }
      };
      request.onerror = () => reject(new Error("Upload failed. Check that the RecliQ API is running."));
      request.onload = () => {
        rememberXhrSession(request);
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
    return this.request<PrecheckResult>("/analysis/precheck", { method: "POST", body: JSON.stringify(payload) });
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

  async recordDecision(payload: { value_1: string; value_2: string; decision: "accept" | "reject"; job_id?: string; column_hint?: string; confidence?: number; note?: string }): Promise<void> {
    await this.request("/aliases/decisions", { method: "POST", body: JSON.stringify(payload) });
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

  async listJobs(): Promise<Job[]> {
    const result = await this.request<{ jobs: Job[] }>("/jobs");
    return result.jobs;
  }

  async getJob(jobId: string): Promise<Job> {
    return this.request<Job>(`/jobs/${jobId}`);
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

  async getReportPreview(jobId: string, category: PreviewCategory, offset = 0, scope: ReportScope = {}): Promise<ReportPreview> {
    const params = new URLSearchParams({ category, offset: String(offset), limit: "25" });
    if (scope.filePairId) params.append("file_pair_id", scope.filePairId);
    if (scope.sheetRuleId) params.append("sheet_rule_id", scope.sheetRuleId);
    return this.request<ReportPreview>(`/reports/job/${jobId}/preview?${params.toString()}`);
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
        "Content-Type": "application/json"
      })
    });
    rememberSession(response);
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
