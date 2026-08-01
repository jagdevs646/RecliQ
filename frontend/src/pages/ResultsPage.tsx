import { AlertTriangle, ArrowLeft, ArrowRight, CheckCircle2, Download, Eye, FileWarning, Search, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../services/api";
import type { Job, PreviewCategory, ReconciliationSummary, ReportPreview } from "../types";

interface Props { job: Job | null; onNewReconciliation: () => void; }

const emptySummary: ReconciliationSummary = { report_rows: 0, only_in_file_1: 0, only_in_file_2: 0, confidence_review: 0 };

export function ResultsPage({ job, onNewReconciliation }: Props) {
  const [summary, setSummary] = useState<ReconciliationSummary>(emptySummary);
  const [preview, setPreview] = useState<ReportPreview | null>(null);
  const [category, setCategory] = useState<PreviewCategory>("discrepancies");
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState("");
  const [loadingPreview, setLoadingPreview] = useState(false);

  const file1Name = job?.input_file_1_name || "File 1";
  const file2Name = job?.input_file_2_name || "File 2";

  useEffect(() => {
    setPreview(null); setMessage("");
    if (!job?.id || job.status !== "completed") return;
    api.getReportSummary(job.id).then(setSummary).catch((error: Error) => setMessage(error.message));
  }, [job?.id, job?.status]);

  useEffect(() => {
    if (!job?.id || !preview) return;
    setLoadingPreview(true);
    api.getReportPreview(job.id, category, page * 25).then(setPreview).catch((error: Error) => setMessage(error.message)).finally(() => setLoadingPreview(false));
  }, [category, page]);

  const matched = summary.fully_matched_records ?? Math.max(0, (summary.matched_records ?? 0) - summary.report_rows - summary.confidence_review);
  const total = summary.source_records ?? Math.max(0, matched + summary.report_rows + summary.only_in_file_1 + summary.only_in_file_2 + summary.confidence_review);
  const accuracy = total ? (matched / total) * 100 : 0;
  const chartTotal = Math.max(1, matched + summary.report_rows + summary.only_in_file_1 + summary.only_in_file_2);
  const chartStyle = { background: `conic-gradient(#1d9a6c 0 ${(matched / chartTotal) * 100}%, #ef6f63 ${(matched / chartTotal) * 100}% ${((matched + summary.report_rows) / chartTotal) * 100}%, #f5b54c ${((matched + summary.report_rows) / chartTotal) * 100}% ${((matched + summary.report_rows + summary.only_in_file_1) / chartTotal) * 100}%, #64748b ${((matched + summary.report_rows + summary.only_in_file_1) / chartTotal) * 100}% 100%)` };
  const cards: Array<{ category: PreviewCategory; title: string; label: string; count: number; icon: typeof AlertTriangle; tone: string }> = [
    { category: "discrepancies", title: "Discrepancies", label: "Items with mismatched values", count: summary.report_rows, icon: AlertTriangle, tone: "coral" },
    { category: "only_file_1", title: `Present in ${file1Name} only`, label: `Found only in ${file1Name}`, count: summary.only_in_file_1, icon: FileWarning, tone: "amber" },
    { category: "only_file_2", title: `Present in ${file2Name} only`, label: `Found only in ${file2Name}`, count: summary.only_in_file_2, icon: FileWarning, tone: "slate" },
    { category: "review", title: "Confidence review", label: "Possible text matches", count: summary.confidence_review, icon: Eye, tone: "violet" }
  ];
  const displayedRows = useMemo(() => preview?.rows.filter((row) => Object.values(row).some((value) => String(value ?? "").toLowerCase().includes(query.toLowerCase()))) ?? [], [preview, query]);

  async function openPreview(nextCategory: PreviewCategory) {
    if (!job) return;
    setCategory(nextCategory); setPage(0); setQuery(""); setLoadingPreview(true);
    try { setPreview(await api.getReportPreview(job.id, nextCategory)); } catch (error) { setMessage(error instanceof Error ? error.message : "Could not load report preview"); } finally { setLoadingPreview(false); }
  }

  async function download() { if (!job) return; try { await api.downloadJobReport(job.id); } catch (error) { setMessage(error instanceof Error ? error.message : "Download failed"); } }

  if (!job || job.status !== "completed") return <section className="page"><div className="empty-state"><h2>No completed report selected</h2><p>Complete a reconciliation to see its results dashboard.</p><button type="button" className="primary" onClick={onNewReconciliation}>Start reconciliation</button></div></section>;

  return <section className="page results-page">
    <div className="page-title"><div><span className="eyebrow">Reconciliation complete</span><h1>Results dashboard</h1><p>Review exceptions, inspect the records behind them, and download the detailed workbook.</p></div><button type="button" className="primary" onClick={download}><Download size={18} />Download Excel report</button></div>
    <div className="summary-grid"><Metric label="Records compared" value={total} icon={<ShieldCheck size={20} />} tone="blue" /><Metric label="Fully matched" value={matched} icon={<CheckCircle2 size={20} />} tone="green" /><Metric label="Discrepancies found" value={summary.report_rows} icon={<AlertTriangle size={20} />} tone="coral" /><Metric label="Reconciliation accuracy" value={total ? `${accuracy.toFixed(2)}%` : "—"} icon={<ShieldCheck size={20} />} tone="violet" /></div>
    <div className="results-insights"><div className="chart-card"><div><h2>Result distribution</h2><p>Matched records and exceptions in this run.</p></div><div className="donut-wrap"><div className="donut" style={chartStyle}><span>{total}</span><small>records</small></div><ul className="chart-legend"><li><i className="legend-green" />Matched <strong>{matched}</strong></li><li><i className="legend-coral" />Discrepancies <strong>{summary.report_rows}</strong></li><li><i className="legend-amber" />{file1Name} only <strong>{summary.only_in_file_1}</strong></li><li><i className="legend-slate" />{file2Name} only <strong>{summary.only_in_file_2}</strong></li></ul></div></div><div className="result-note"><ShieldCheck size={23} /><div><h2>Report ready</h2><p>The detailed Excel workbook contains the complete reconciliation, including every exception and selected context field.</p><button type="button" className="text-command" onClick={download}><Download size={16} />Download detailed report</button></div></div></div>
    <section><div className="section-heading"><div><h2>Exception categories</h2><p>Open any category to inspect a paginated preview of up to 25 report rows at a time.</p></div></div><div className="result-card-grid">{cards.map(({ category: cardCategory, title, label, count, icon: Icon, tone }) => <article className={`result-card tone-${tone}`} key={cardCategory}><Icon size={20} /><span>{title}</span><strong>{count.toLocaleString()}</strong><p>{label}</p><button type="button" className="text-command" onClick={() => openPreview(cardCategory)}><Eye size={16} />View details</button></article>)}</div></section>
    {preview && <section className="preview-panel"><div className="section-heading"><div><h2>{preview.sheet_name}</h2><p>{preview.total_rows.toLocaleString()} rows in the workbook</p></div><label className="search-field"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter visible rows" /></label></div><div className="table-scroll"><table><thead><tr>{preview.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{loadingPreview ? <tr><td colSpan={Math.max(1, preview.columns.length)}>Loading preview...</td></tr> : displayedRows.length ? displayedRows.map((row, index) => <tr key={index}>{preview.columns.map((column) => <td key={column}>{row[column] ?? "—"}</td>)}</tr>) : <tr><td colSpan={Math.max(1, preview.columns.length)}>No visible rows match this filter.</td></tr>}</tbody></table></div><div className="pagination"><span>Showing {Math.min(preview.offset + 1, preview.total_rows)}–{Math.min(preview.offset + preview.rows.length, preview.total_rows)} of {preview.total_rows}</span><div><button type="button" className="icon-button" onClick={() => setPage((current) => Math.max(0, current - 1))} disabled={page === 0 || loadingPreview} title="Previous preview page"><ArrowLeft size={16} /></button><button type="button" className="icon-button" onClick={() => setPage((current) => current + 1)} disabled={preview.offset + preview.rows.length >= preview.total_rows || loadingPreview} title="Next preview page"><ArrowRight size={16} /></button></div></div></section>}
    {message && <p className="error-text">{message}</p>}
  </section>;
}

function Metric({ label, value, icon, tone }: { label: string; value: string | number; icon: React.ReactNode; tone: string }) { return <article className={`summary-card tone-${tone}`}><span className="metric-icon">{icon}</span><span>{label}</span><strong>{typeof value === "number" ? value.toLocaleString() : value}</strong></article>; }
