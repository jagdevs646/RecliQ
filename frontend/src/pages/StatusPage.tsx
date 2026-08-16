import { Ban, Check, Circle, Download, FileSpreadsheet, Loader2, RefreshCw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ProgressBar } from "../components/ProgressBar";
import { StatusBadge } from "../components/StatusBadge";
import { api } from "../services/api";
import type { Job } from "../types";

interface Props {
  job: Job | null;
  onJobUpdate: (job: Job) => void;
  onViewResults: () => void;
}

const stages = [
  { label: "Preparing", threshold: 1 },
  { label: "Reading Excel", threshold: 35 },
  { label: "Reconciling", threshold: 85 },
  { label: "Generating report", threshold: 99 },
  { label: "Completed", threshold: 100 }
];

export function StatusPage({ job, onJobUpdate, onViewResults }: Props) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const redirectedJob = useRef<string | null>(null);

  async function refresh() {
    if (!job) {
      return;
    }
    setBusy(true);
    try {
      onJobUpdate(await api.getJob(job.id));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not refresh job");
    } finally {
      setBusy(false);
    }
  }

  async function handleAbort() {
    if (!job || !window.confirm("Are you sure you want to abort this reconciliation? Any progress will be stopped.")) {
      return;
    }
    setCancelling(true);
    try {
      const updated = await api.cancelJob(job.id);
      onJobUpdate(updated);
      setMessage("Reconciliation has been cancelled.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not cancel reconciliation");
    } finally {
      setCancelling(false);
    }
  }

  useEffect(() => {
    if (!job || job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
      return;
    }
    const timer = window.setInterval(() => refresh(), 1000);
    return () => window.clearInterval(timer);
  }, [job?.id, job?.status]);

  useEffect(() => {
    if (job?.status === "completed" && redirectedJob.current !== job.id) {
      redirectedJob.current = job.id;
      const timer = window.setTimeout(onViewResults, 700);
      return () => window.clearTimeout(timer);
    }
  }, [job?.id, job?.status, onViewResults]);

  async function download() {
    if (!job) {
      return;
    }
    try {
      await api.downloadJobReport(job.id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Download failed");
    }
  }

  const isOngoing = job?.status === "queued" || job?.status === "processing";

  return (
    <section className="page">
      <div className="page-title">
        <div>
          <span className="eyebrow">Live reconciliation</span>
          <h1>
            {job?.status === "completed"
              ? "Your report is ready"
              : job?.status === "cancelled"
              ? "Reconciliation cancelled"
              : job?.status === "failed"
              ? "Reconciliation failed"
              : "Reconciling your workbooks"}
          </h1>
          <p>
            {job?.status === "cancelled"
              ? "This reconciliation was aborted before completion. No report was generated."
              : "RecliQ updates this page automatically while your report is being generated."}
          </p>
        </div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          {isOngoing && (
            <button
              type="button"
              className="danger-button"
              onClick={handleAbort}
              disabled={cancelling}
              style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem" }}
            >
              {cancelling ? <Loader2 size={16} className="animate-spin" /> : <Ban size={16} />}
              Abort Reconciliation
            </button>
          )}
          <button type="button" className="secondary" onClick={refresh} disabled={!job || busy}>
            <RefreshCw size={16} />
            Refresh
          </button>
        </div>
      </div>

      {!job ? (
        <div className="empty-state">No active job selected.</div>
      ) : (<>
        <div className="status-layout">
          <div className="live-progress-card">
            <div className="progress-heading">
              <div>
                <span className="eyebrow">Progress</span>
                <strong>{job.status === "cancelled" ? "Cancelled" : `${job.progress}% complete`}</strong>
              </div>
              <StatusBadge status={job.status} />
            </div>
            <ProgressBar value={job.status === "cancelled" ? 0 : job.progress} />
            <ol className="process-timeline">{stages.map((stage) => {
              const failed = (job.status === "failed" || job.status === "cancelled") && stage.threshold >= job.progress;
              const complete = job.status === "completed" || (job.status !== "cancelled" && job.progress >= stage.threshold);
              const active = !failed && !complete && job.progress >= Math.max(0, stage.threshold - 35);
              return (
                <li key={stage.label} className={complete ? "is-complete" : failed ? "is-failed" : active ? "is-active" : ""}>
                  <span>{complete ? <Check size={15} /> : failed ? <X size={15} /> : active ? <Loader2 size={15} /> : <Circle size={13} />}</span>
                  <strong>{stage.label}</strong>
                  {active && <small>Working...</small>}
                </li>
              );
            })}</ol>
          </div>
          <aside className="job-context-card">
            <FileSpreadsheet size={24} />
            <h2>Run details</h2>
            <dl>
              {job.input_file_1_name && <div><dt>Source file</dt><dd>{job.input_file_1_name}</dd></div>}
              {job.input_file_2_name && <div><dt>Destination file</dt><dd>{job.input_file_2_name}</dd></div>}
              <div><dt>Type</dt><dd>{job.job_type === "gst" ? "GST invoices" : "General"}</dd></div>
              <div><dt>Orientation</dt><dd>{job.orientation === "horizontal" ? "Row headers" : "Column headers"}</dd></div>
              <div><dt>Job ID</dt><dd>{job.id.slice(0, 8)}</dd></div>
            </dl>
            {job.status === "completed" && (
              <>
                <button type="button" className="primary full-width" onClick={onViewResults}>View results</button>
                <button type="button" className="text-command centered" onClick={download}><Download size={16} />Download Excel report</button>
              </>
            )}
            {isOngoing && (
              <button
                type="button"
                className="text-command centered"
                style={{ color: "#ef4444", marginTop: "1rem" }}
                onClick={handleAbort}
                disabled={cancelling}
              >
                <Ban size={16} />
                Cancel this run
              </button>
            )}
          </aside>
        </div>
        {job.error_message && <p className="error-text">{job.error_message}</p>}
      </>)}
      {message && <p className="info-text" style={{ marginTop: "1rem" }}>{message}</p>}
    </section>
  );
}
