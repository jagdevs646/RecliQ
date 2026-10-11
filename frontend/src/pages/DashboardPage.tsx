import { AlertCircle, ArrowRight, BarChart3, CheckCircle2, Clock3, FileSpreadsheet, History, Layers3, Play, Plus, RefreshCw, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { formatServerTime } from "../lib/formats";
import { api } from "../services/api";
import type { Job, WorkspaceOverview } from "../types";
import { OpenJobButton } from "../components/OpenJobButton";
import { StatusBadge } from "../components/StatusBadge";

interface Props {
  onNavigateUpload: () => void;
  onNavigateGst: () => void;
  onNavigateHistory: () => void;
  onNavigateSaved: () => void;
  onOpenJob: (job: Job) => void;
  onRunSetup: (templateId: string) => void;
}

type Period = "7d" | "30d" | "all";

export function DashboardPage({ onNavigateUpload, onNavigateGst, onNavigateHistory, onNavigateSaved, onOpenJob, onRunSetup }: Props) {
  const [period, setPeriod] = useState<Period>("7d");
  const [overview, setOverview] = useState<WorkspaceOverview | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function load(nextPeriod = period) {
    setLoading(true); setError("");
    try { setOverview(await api.workspaceOverview(nextPeriod)); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not load workspace overview."); }
    finally { setLoading(false); }
  }

  useEffect(() => { void load(period); }, [period]);

  const metrics = overview?.metrics;
  const maxRuns = Math.max(1, ...(overview?.activity.map((day) => day.runs) ?? [0]));
  const workspaceName = overview?.workspace.user?.display_name || overview?.workspace.label || "Guest Workspace";
  const canReconcile = overview?.features.basic_reconciliation !== false;
  const canRunGst = overview?.features.gst_reconciliation !== false;
  const canUseSavedSetups = overview?.features.saved_setups !== false;

  return <section className="page dashboard-page">
      <div className="page-title dashboard-heading">
      <div><span className="eyebrow">Reconciliation control center</span><h1>Good to see you, {workspaceName}</h1><p>Workspace activity, open reviews, and reusable setups in one place.</p></div>
      <div className="page-actions"><button type="button" className="secondary" onClick={onNavigateHistory}><History size={17} />History</button>{canReconcile && <button type="button" className="primary" onClick={onNavigateUpload}><Plus size={18} />New reconciliation</button>}{!canReconcile && canRunGst && <button type="button" className="primary" onClick={onNavigateGst}><Plus size={18} />GST reconciliation</button>}</div>
    </div>

    {error && <div className="error-callout" role="alert"><AlertCircle size={18} /><span>{error}</span><button type="button" className="text-command" onClick={() => void load()}><RefreshCw size={15} />Retry</button></div>}

    <div className="dashboard-filter-row">
      <div className="workspace-chip"><span className={`workspace-dot ${overview?.workspace.type === "account" ? "is-account" : ""}`} />{overview?.workspace.type === "account" ? "Personal workspace" : "Guest workspace"}</div>
      <div className="segmented-control" role="group" aria-label="Dashboard period">
        {([ ["7d", "7 days"], ["30d", "30 days"], ["all", "All history"] ] as const).map(([value, label]) => <button key={value} type="button" className={period === value ? "is-active" : ""} aria-pressed={period === value} onClick={() => setPeriod(value)}>{label}</button>)}
      </div>
    </div>

    <div className="dashboard-kpis" aria-live="polite">
      <Kpi icon={<Layers3 size={18} />} label="Reconciliation runs" value={metrics?.runs} note={`${metrics?.completed_runs ?? 0} completed · ${metrics?.failed_runs ?? 0} failed`} loading={loading} />
      <Kpi icon={<CheckCircle2 size={18} />} label="Matched records" value={metrics?.matched_records} note="Across completed runs" loading={loading} tone="green" />
      <Kpi icon={<ShieldAlert size={18} />} label="Exceptions flagged" value={metrics?.exceptions_flagged} note="Missing records and field differences" loading={loading} tone="coral" />
      <Kpi icon={<Clock3 size={18} />} label="Awaiting review" value={metrics?.awaiting_review} note="Near-match decisions still pending" loading={loading} tone="amber" />
    </div>

    <div className="dashboard-main-grid">
      <section className="dashboard-panel activity-panel">
        <div className="section-heading"><div><h2>Reconciliation activity</h2><p>{period === "all" ? "Daily activity over the last 7 days" : `Daily runs over ${period === "7d" ? "7" : "30"} days`}</p></div><BarChart3 size={19} /></div>
        <div className={`activity-chart ${period === "30d" ? "is-month" : ""}`} role="img" aria-label="Daily reconciliation runs">
          {(overview?.activity ?? []).map((day) => <div className="activity-bar-slot" key={day.date} title={`${day.date}: ${day.runs} run${day.runs === 1 ? "" : "s"}`}>
            <strong>{day.runs || ""}</strong><span className="activity-bar" style={{ height: `${Math.max(3, (day.runs / maxRuns) * 100)}%` }} /><small>{new Date(`${day.date}T12:00:00`).toLocaleDateString(undefined, period === "30d" ? { day: "numeric" } : { weekday: "short" })}</small>
          </div>)}
          {!loading && !overview?.activity.length && <p className="chart-empty">No activity in this period.</p>}
        </div>
        <p className="metric-definition">Runs include queued, failed and completed jobs. Matched records are counted from completed reports; exceptions count missing records plus field discrepancies. Review counts exclude decisions already made.</p>
      </section>

      <section className="dashboard-panel quick-panel">
        <div className="section-heading"><div><h2>Start a reconciliation</h2><p>Choose a workflow to get started.</p></div></div>
        {canReconcile && <button type="button" className="quick-action" onClick={onNavigateUpload}><span className="quick-icon"><FileSpreadsheet size={19} /></span><span><strong>New reconciliation</strong><small>Configure files, keys and column rules</small></span><ArrowRight size={17} /></button>}
        {canUseSavedSetups && <button type="button" className="quick-action" onClick={onNavigateSaved}><span className="quick-icon is-blue"><History size={19} /></span><span><strong>Use a saved setup</strong><small>Run a previous configuration on new files</small></span><ArrowRight size={17} /></button>}
        {canRunGst && <button type="button" className="quick-action" onClick={onNavigateGst}><span className="quick-icon is-amber"><ShieldAlert size={19} /></span><span><strong>GST reconciliation</strong><small>Compare GST invoice and return data</small></span><ArrowRight size={17} /></button>}
        {!canReconcile && !canRunGst && !canUseSavedSetups && <p className="dashboard-empty">Reconciliation actions are currently restricted for this workspace.</p>}
      </section>
    </div>

    <div className="dashboard-lower-grid">
      <section className="dashboard-panel attention-panel">
        <div className="section-heading"><div><h2>Requires your attention</h2><p>Failed or in-progress runs and unresolved match decisions.</p></div><button type="button" className="text-command" onClick={onNavigateHistory}>View history <ArrowRight size={15} /></button></div>
        {loading ? <p className="dashboard-empty">Loading workspace activity…</p> : overview?.attention.length ? <div className="attention-list">{overview.attention.map((job) => <article className="attention-row" key={job.id}>
          <div className="attention-icon">{job.status === "failed" ? <AlertCircle size={18} /> : job.status === "queued" || job.status === "processing" ? <Clock3 size={18} /> : <ShieldAlert size={18} />}</div>
          <div className="attention-content"><button className="row-link" type="button" onClick={() => onOpenJob(job)}>{job.display_name || `${job.input_file_1_name || "File 1"} vs ${job.input_file_2_name || "File 2"}`}</button><small>{job.job_type === "gst" ? "GST" : "General"} · {formatServerTime(job.created_at)}</small></div>
          <StatusBadge status={job.status} />
          {job.status === "completed" || job.status === "completed_with_errors" ? <span className="attention-count">{job.review_count ?? 0} to review</span> : <span className="attention-count">{job.progress}%</span>}
        </article>)}</div> : <div className="dashboard-empty"><CheckCircle2 size={19} /><span>Nothing needs attention right now.</span></div>}
      </section>

      {canUseSavedSetups && <section className="dashboard-panel setups-panel">
        <div className="section-heading"><div><h2>Recently used setups</h2><p>Re-use saved rules on new files.</p></div><button type="button" className="icon-button" title="Refresh" aria-label="Refresh saved setups" onClick={() => void load()}><RefreshCw size={15} /></button></div>
        {overview?.saved_setups.length ? <div className="setup-shortcuts">{overview.saved_setups.map((setup) => <article className="setup-shortcut" key={setup.id}><div><strong>{setup.name}</strong><small>{setup.current_version ? `Version ${setup.current_version}` : "Saved setup"}{setup.last_run_at ? ` · Last run ${formatServerTime(setup.last_run_at)}` : " · Not run yet"}</small></div><button type="button" className="icon-button" title={`Run ${setup.name}`} aria-label={`Run ${setup.name}`} onClick={() => onRunSetup(setup.id)}><Play size={15} /></button></article>)}</div> : <div className="dashboard-empty compact"><span>No saved setups yet.</span><button type="button" className="text-command" onClick={onNavigateSaved}>View saved setups <ArrowRight size={14} /></button></div>}
      </section>}
    </div>
  </section>;
}

function Kpi({ icon, label, value, note, loading, tone = "teal" }: { icon: React.ReactNode; label: string; value?: number; note: string; loading: boolean; tone?: string }) {
  return <article className={`dashboard-kpi tone-${tone}`}><span className="dashboard-kpi-icon">{icon}</span><span className="dashboard-kpi-label">{label}</span><strong>{loading ? "—" : (value ?? 0).toLocaleString()}</strong><small>{note}</small></article>;
}
