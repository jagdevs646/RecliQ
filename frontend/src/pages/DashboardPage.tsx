import { CheckCircle2, Clock3, FileSpreadsheet, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../services/api";
import type { Job } from "../types";
import { StatusBadge } from "../components/StatusBadge";

interface Props {
  onNavigateUpload: () => void;
  onOpenJob: (job: Job) => void;
}

export function DashboardPage({ onNavigateUpload, onOpenJob }: Props) {
  const [jobs, setJobs] = useState<Job[]>([]);

  useEffect(() => {
    api.listJobs().then(setJobs).catch(() => setJobs([]));
  }, []);

  const completed = jobs.filter((job) => job.status === "completed").length;
  const processing = jobs.filter((job) => job.status === "processing" || job.status === "queued").length;
  const failed = jobs.filter((job) => job.status === "failed").length;

  return (
    <section className="page">
      <div className="page-title">
        <div>
          <h1>Dashboard</h1>
          <p>Recent reconciliation activity and report readiness.</p>
        </div>
        <button className="primary" type="button" onClick={onNavigateUpload}>
          <FileSpreadsheet size={18} />
          New Reconciliation
        </button>
      </div>

      <div className="metric-grid">
        <div className="metric"><CheckCircle2 size={20} /><span>Completed</span><strong>{completed}</strong></div>
        <div className="metric"><Clock3 size={20} /><span>Queued / Processing</span><strong>{processing}</strong></div>
        <div className="metric"><XCircle size={20} /><span>Failed</span><strong>{failed}</strong></div>
      </div>

      <div className="table-panel">
        <h2>Latest Jobs</h2>
        <table>
          <thead>
            <tr>
              <th>Type</th>
              <th>Files</th>
              <th>Status</th>
              <th>Progress</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {jobs.slice(0, 8).map((job) => (
              <tr key={job.id} onClick={() => onOpenJob(job)}>
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

