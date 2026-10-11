import { CalendarDays, Download, Loader2, Mail, Pencil, Play, RefreshCw, Search, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { OpenJobButton } from "../components/OpenJobButton";
import { ReportEmailDialog } from "../components/ReportEmailDialog";
import { StatusBadge } from "../components/StatusBadge";
import { formatServerTime } from "../lib/formats";
import { api } from "../services/api";
import type { Job } from "../types";

interface Props {
  onOpenJob: (job: Job) => void;
  onRerunJob: (job: Job) => void;
  canEmail?: boolean;
}

const PAGE_SIZE = 20;

export function HistoryPage({ onOpenJob, onRerunJob, canEmail = false }: Props) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [sort, setSort] = useState("newest");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [workingId, setWorkingId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [emailJob, setEmailJob] = useState<Job | null>(null);

  async function load() {
    setBusy(true); setMessage("");
    try {
      const result = await api.jobList({ q: query.trim() || undefined, status: status || undefined, date_from: dateFrom || undefined, date_to: dateTo || undefined, sort, page, page_size: PAGE_SIZE });
      setJobs(result.jobs); setTotal(result.total);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load reconciliation history.");
    } finally { setBusy(false); }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => { void load(); }, 250);
    return () => window.clearTimeout(timer);
  }, [query, status, dateFrom, dateTo, sort, page]);

  async function deleteJob(event: React.MouseEvent, job: Job) {
    event.stopPropagation();
    if (!window.confirm(`Delete “${job.display_name || job.id.slice(0, 8)}” and its stored report? This cannot be undone.`)) return;
    setWorkingId(job.id); setMessage("");
    try {
      await api.deleteJob(job.id);
      if (jobs.length === 1 && page > 1) setPage((value) => value - 1);
      else await load();
      setMessage("Reconciliation and its stored report were deleted.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Could not delete reconciliation."); }
    finally { setWorkingId(null); }
  }

  async function downloadReport(event: React.MouseEvent, job: Job) {
    event.stopPropagation(); setWorkingId(job.id); setMessage("");
    try { await api.downloadJobReport(job.id); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Report download failed."); }
    finally { setWorkingId(null); }
  }

  async function renameJob(event: React.MouseEvent, job: Job) {
    event.stopPropagation();
    const name = window.prompt("Reconciliation name", job.display_name || `${job.input_file_1_name || "File 1"} vs ${job.input_file_2_name || "File 2"}`);
    if (!name?.trim() || name.trim() === job.display_name) return;
    setWorkingId(job.id); setMessage("");
    try { await api.renameJob(job.id, name.trim()); await load(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Could not rename reconciliation."); }
    finally { setWorkingId(null); }
  }

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return <section className="page history-page">
    <div className="page-title"><div><span className="eyebrow">Workspace records</span><h1>History</h1><p>Find, revisit, and manage reconciliations in this workspace.</p></div><button type="button" className="secondary" onClick={() => void load()} disabled={busy}><RefreshCw size={16} />Refresh</button></div>
    <div className="history-filters" role="search">
      <label className="search-field"><Search size={16} /><input value={query} onChange={(event) => { setPage(1); setQuery(event.target.value); }} placeholder="Search name or uploaded filename" aria-label="Search reconciliation history" /></label>
      <select aria-label="Filter by status" value={status} onChange={(event) => { setPage(1); setStatus(event.target.value); }}><option value="">All statuses</option><option value="queued">Queued</option><option value="processing">Processing</option><option value="completed">Completed</option><option value="completed_with_errors">Completed with errors</option><option value="failed">Failed</option></select>
      <label className="date-filter"><CalendarDays size={15} /><input type="date" value={dateFrom} onChange={(event) => { setPage(1); setDateFrom(event.target.value); }} aria-label="Start date" /></label>
      <label className="date-filter"><CalendarDays size={15} /><input type="date" value={dateTo} onChange={(event) => { setPage(1); setDateTo(event.target.value); }} aria-label="End date" /></label>
      <select aria-label="Sort history" value={sort} onChange={(event) => { setPage(1); setSort(event.target.value); }}><option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="name">Name</option></select>
    </div>
    {message && <p className="info-text" role="status">{message}</p>}
    <div className="table-panel history-table-panel">
      <div className="table-caption">{busy ? "Loading…" : `${total.toLocaleString()} reconciliation${total === 1 ? "" : "s"}`}</div>
      <div className="table-scroll-x"><table>
        <thead><tr><th>Reconciliation</th><th>Type</th><th>Files</th><th>Status</th><th>Progress</th><th>Created</th><th>Actions</th></tr></thead>
        <tbody>{busy && jobs.length === 0 ? <tr><td colSpan={7} className="table-empty"><Loader2 size={18} className="animate-spin" /> Loading history…</td></tr> : jobs.length === 0 ? <tr><td colSpan={7} className="table-empty">No reconciliations match these filters.</td></tr> : jobs.map((job) => <tr key={job.id} className="clickable-row" onClick={() => onOpenJob(job)}>
          <td><strong>{job.display_name || `${job.input_file_1_name || "File 1"} vs ${job.input_file_2_name || "File 2"}`}</strong><small className="history-job-id">{job.id.slice(0, 8)}</small></td>
          <td>{job.job_type === "gst" ? "GST" : "General"}</td>
          <td className="cell-files"><OpenJobButton job={job} onOpen={onOpenJob} /></td>
          <td><StatusBadge status={job.status} /></td><td>{job.progress}%</td><td>{formatServerTime(job.created_at)}</td>
          <td><div className="history-row-actions">
            <button type="button" className="icon-button" title="Rename" aria-label="Rename reconciliation" disabled={workingId === job.id} onClick={(event) => void renameJob(event, job)}><Pencil size={15} /></button>
            {job.report_id && <button type="button" className="icon-button" title="Download report" aria-label="Download report" disabled={workingId === job.id} onClick={(event) => void downloadReport(event, job)}>{workingId === job.id ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}</button>}
            {canEmail && job.report_id && <button type="button" className="icon-button" title="Email report" aria-label="Email report" onClick={(event) => { event.stopPropagation(); setEmailJob(job); }}><Mail size={15} /></button>}
            <button type="button" className="icon-button" title="Run again with this setup" aria-label="Run reconciliation again" onClick={(event) => { event.stopPropagation(); onRerunJob(job); }}><Play size={15} /></button>
            <button type="button" className="icon-button is-danger" title="Delete reconciliation" aria-label="Delete reconciliation" disabled={workingId === job.id} onClick={(event) => void deleteJob(event, job)}><Trash2 size={15} /></button>
          </div></td>
        </tr>)}</tbody>
      </table></div>
      <div className="pagination"><span>Page {page} of {pages} · {total.toLocaleString()} records</span><div><button type="button" className="secondary compact" disabled={page <= 1 || busy} onClick={() => setPage((value) => value - 1)}>Previous</button><button type="button" className="secondary compact" disabled={page >= pages || busy} onClick={() => setPage((value) => value + 1)}>Next</button></div></div>
    </div>
    {emailJob && <ReportEmailDialog job={emailJob} onClose={() => setEmailJob(null)} />}
  </section>;
}
