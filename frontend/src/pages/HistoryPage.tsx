import { Loader2, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { StatusBadge } from "../components/StatusBadge";
import { api } from "../services/api";
import type { Job } from "../types";

interface Props {
  onOpenJob: (job: Job) => void;
}

export function HistoryPage({ onOpenJob }: Props) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [busy, setBusy] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [message, setMessage] = useState("");

  async function load() {
    setBusy(true);
    try {
      setJobs(await api.listJobs());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load history");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(e: React.MouseEvent, jobId: string) {
    e.stopPropagation();
    if (!window.confirm("Delete this reconciliation record and its files?")) {
      return;
    }
    setDeletingId(jobId);
    try {
      await api.deleteJob(jobId);
      setJobs((prev) => prev.filter((j) => j.id !== jobId));
      setMessage("Record deleted successfully.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not delete record");
    } finally {
      setDeletingId(null);
    }
  }

  async function handleClearAll() {
    if (!window.confirm("Are you sure you want to clear all history records and delete all stored reports?")) {
      return;
    }
    setBusy(true);
    try {
      await api.clearHistory();
      setJobs([]);
      setMessage("All history records and files have been cleared.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not clear history");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, []);

  return (
    <section className="page">
      <div className="page-title">
        <div>
          <h1>History</h1>
          <p>
            Past reconciliation jobs and reports. Storage automatically retains a maximum of 20 completed records.
          </p>
        </div>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          {jobs.length > 0 && (
            <button
              type="button"
              className="danger-button"
              onClick={handleClearAll}
              disabled={busy}
              style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem" }}
            >
              <Trash2 size={16} />
              Clear History
            </button>
          )}
          <button type="button" className="secondary" onClick={load} disabled={busy}>
            <RefreshCw size={16} />
            Refresh
          </button>
        </div>
      </div>

      {message && <p className="info-text" style={{ marginBottom: "1rem" }}>{message}</p>}

      <div className="table-panel">
        <div style={{ padding: "0.75rem 1rem", borderBottom: "1px solid var(--color-border, #e2e8f0)", color: "var(--color-muted, #64748b)", fontSize: "0.875rem" }}>
          Stored records: <strong>{jobs.length}</strong> / 20 max
        </div>
        <table>
          <thead>
            <tr>
              <th>Job</th>
              <th>Type</th>
              <th>Files</th>
              <th>Status</th>
              <th>Progress</th>
              <th>Created</th>
              <th style={{ width: "80px", textAlign: "center" }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 ? (
              <tr>
                <td colSpan={7} style={{ textAlign: "center", padding: "2rem", color: "var(--color-muted, #64748b)" }}>
                  No past reconciliation records found.
                </td>
              </tr>
            ) : (
              jobs.map((job) => (
                <tr key={job.id} onClick={() => onOpenJob(job)} style={{ cursor: "pointer" }}>
                  <td><strong>{job.id.slice(0, 8)}</strong></td>
                  <td>{job.job_type === "gst" ? "GST Invoices" : "General"}</td>
                  <td>{job.input_file_1_name && job.input_file_2_name ? `${job.input_file_1_name} vs ${job.input_file_2_name}` : "—"}</td>
                  <td><StatusBadge status={job.status} /></td>
                  <td>{job.progress}%</td>
                  <td>{new Date(job.created_at).toLocaleString()}</td>
                  <td style={{ textAlign: "center" }}>
                    <button
                      type="button"
                      className="icon-button"
                      title="Delete record"
                      style={{ color: "#ef4444" }}
                      disabled={deletingId === job.id}
                      onClick={(e) => handleDelete(e, job.id)}
                    >
                      {deletingId === job.id ? <Loader2 size={16} className="animate-spin" /> : <Trash2 size={16} />}
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

