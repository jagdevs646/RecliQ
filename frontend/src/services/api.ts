import type { GstConfiguration, Job, PreviewCategory, ReconciliationSummary, ReportPreview, RuleMapping, UploadedFile } from "../types";

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
      throw new Error(text || response.statusText);
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

  async getColumns(fileId: string, orientation: string): Promise<string[]> {
    const result = await this.request<{ columns: string[] }>(`/files/${fileId}/columns?orientation=${encodeURIComponent(orientation)}`);
    return result.columns;
  }

  async startGeneric(payload: {
    file_1_id: string;
    file_2_id: string;
    key_file_1: string;
    key_file_2: string;
    rules: RuleMapping[];
    orientation: string;
    include_columns_file_1: string[];
    include_columns_file_2: string[];
  }): Promise<Job> {
    return this.request<Job>("/reconciliation/generic", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  }

  async startGst(payload: { file_1_id: string; file_2_id: string; orientation: string; text_threshold: number }): Promise<Job> {
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

  async getReportPreview(jobId: string, category: PreviewCategory, offset = 0): Promise<ReportPreview> {
    return this.request<ReportPreview>(`/reports/job/${jobId}/preview?category=${category}&offset=${offset}&limit=25`);
  }

  reportUrl(jobId: string): string {
    return `${API_BASE}/reports/job/${jobId}/download`;
  }

  async downloadJobReport(jobId: string): Promise<void> {
    const response = await fetch(this.reportUrl(jobId), { credentials: "include", headers: sessionHeaders() });
    rememberSession(response);
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "RecliQ_Reconciliation_Report.xlsx";
    link.click();
    URL.revokeObjectURL(url);
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
