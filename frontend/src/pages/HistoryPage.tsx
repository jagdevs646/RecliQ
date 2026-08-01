import { RefreshCw } from "lucide-react";
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

  async function load() {
    setBusy(true);
    try {
      setJobs(await api.listJobs());
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
          <p>Past reconciliation jobs and generated reports.</p>
        </div>
        <button type="button" className="secondary" onClick={load} disabled={busy}>
          <RefreshCw size={16} />
          Refresh
        </button>
      </div>
      <div className="table-panel">
        <table>
          <thead>
            <tr>
              <th>Job</th>
              <th>Type</th>
              <th>Files</th>
              <th>Status</th>
              <th>Progress</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id} onClick={() => onOpenJob(job)}>
                <td>{job.id.slice(0, 8)}</td>
                <td>{job.job_type === "gst" ? "GST Invoices" : "General"}</td>
                <td>{job.input_file_1_name && job.input_file_2_name ? `${job.input_file_1_name} vs ${job.input_file_2_name}` : "—"}</td>
                <td><StatusBadge status={job.status} /></td>
                <td>{job.progress}%</td>
                <td>{new Date(job.created_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

