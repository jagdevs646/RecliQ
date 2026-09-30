import { Loader2, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { OpenJobButton } from "../components/OpenJobButton";
import { StatusBadge } from "../components/StatusBadge";
import { formatServerTime } from "../lib/formats";
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
          <span className="eyebrow">Overview</span>
          <h1>History</h1>
          <p>Past reconciliations and their reports. The latest 20 are kept.</p>
        </div>
        {/* Clearing everything is rare and destructive: a quiet command, not the page's main button. */}
        <div className="page-actions">
          <button type="button" className="secondary" onClick={load} disabled={busy}>
            <RefreshCw size={16} />
            Refresh
          </button>
          {jobs.length > 0 && (
            <button type="button" className="text-command is-danger" onClick={handleClearAll} disabled={busy}>
              <Trash2 size={16} />
              Clear history
            </button>
          )}
        </div>
      </div>

      {message && <p className="info-text" style={{ marginBottom: "1rem" }}>{message}</p>}

      <div className="table-panel">
        <div className="table-caption">
          Stored records: <strong>{jobs.length}</strong> / 20 max
        </div>
        <div className="table-scroll-x">
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
                <td colSpan={7} className="table-empty">
                  No past reconciliation records found.
                </td>
              </tr>
            ) : (
              jobs.map((job) => (
                <tr key={job.id} className="clickable-row" onClick={() => onOpenJob(job)}>
                  <td><strong>{job.id.slice(0, 8)}</strong></td>
                  <td>{job.job_type === "gst" ? "GST invoices" : "General"}</td>
                  <td className="cell-files"><OpenJobButton job={job} onOpen={onOpenJob} /></td>
                  <td><StatusBadge status={job.status} /></td>
                  <td>{job.progress}%</td>
                  <td>{formatServerTime(job.created_at)}</td>
                  <td style={{ textAlign: "center" }}>
                    <button
                      type="button"
                      className="icon-button is-danger"
                      title="Delete record"
                      aria-label={`Delete record ${job.id.slice(0, 8)}`}
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
      </div>
    </section>
  );
}

