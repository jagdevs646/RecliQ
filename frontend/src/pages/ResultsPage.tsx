import { AlertTriangle, ArrowLeft, ArrowRight, BookmarkPlus, CheckCircle2, Download, Eye, FileWarning, HelpCircle, Search, ShieldCheck, Settings2, Split, ThumbsDown, ThumbsUp, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../services/api";
import type { Job, PreviewCategory, ReconciliationSummary, ReportPreview, ReportCustomConfig, ReportScope } from "../types";
import { ReportCustomizer } from "../components/ReportCustomizer";

interface Props { job: Job | null; onNewReconciliation: () => void; }

const emptySummary: ReconciliationSummary = { report_rows: 0, only_in_file_1: 0, only_in_file_2: 0, confidence_review: 0 };

export function ResultsPage({ job, onNewReconciliation }: Props) {
  // The job summary is also the manifest: aggregate counts plus file pairs and sheet rules.
  const [jobSummary, setJobSummary] = useState<ReconciliationSummary>(emptySummary);
  const [filePairId, setFilePairId] = useState("");
  const [ruleIndex, setRuleIndex] = useState(-1);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [preview, setPreview] = useState<ReportPreview | null>(null);
  const [category, setCategory] = useState<PreviewCategory>("discrepancies");
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState("");
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [viewStage, setViewStage] = useState<"results" | "customize" | "download">("results");
  const [customConfig, setCustomConfig] = useState<ReportCustomConfig | null>(null);
  // Reviewer decisions on proposed matches (evidence for alias suggestions).
  const [decisions, setDecisions] = useState<Record<string, "accept" | "reject">>({});
  const [savedNote, setSavedNote] = useState("");

  const filePairs = jobSummary.file_pairs ?? [];
  const isMultiPair = filePairs.length > 1;
  const activePair = filePairs.find((pair) => pair.file_pair_id === filePairId);
  const rulesInScope = useMemo(() => (jobSummary.sheet_rules ?? []).filter((rule) => !filePairId || rule.file_pair_id === filePairId), [jobSummary, filePairId]);
  const activeRule = ruleIndex >= 0 ? rulesInScope[ruleIndex] : undefined;
  const summary = activeRule ? { ...emptySummary, ...activeRule.summary } : activePair ? { ...emptySummary, ...activePair.summary } : jobSummary;
  const scope: ReportScope = { filePairId: activeRule?.file_pair_id ?? (filePairId || undefined), sheetRuleId: activeRule?.sheet_rule_id };
  const failedRules = (jobSummary.sheet_rules ?? []).filter((rule) => rule.status === "failed");

  const file1Name = activeRule?.source_file || activePair?.source_file || job?.input_file_1_name || "File 1";
  const file2Name = activeRule?.destination_file || activePair?.destination_file || job?.input_file_2_name || "File 2";

  useEffect(() => {
    setPreview(null); setPreviewOpen(false); setMessage(""); setFilePairId(""); setRuleIndex(-1);
    if (!job?.id || !["completed", "completed_with_errors"].includes(job.status)) return;
    api.getReportSummary(job.id).then(setJobSummary).catch((error: Error) => setMessage(error.message));
  }, [job?.id, job?.status]);

  useEffect(() => {
    if (!job?.id || !previewOpen) return;
    setLoadingPreview(true);
    api.getReportPreview(job.id, category, page * 25, scope).then(setPreview).catch((error: Error) => setMessage(error.message)).finally(() => setLoadingPreview(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, previewOpen, category, page, scope.filePairId, scope.sheetRuleId]);

  function selectFilePair(nextFilePairId: string) {
    setFilePairId(nextFilePairId); setRuleIndex(-1); setPage(0);
  }

  function selectRule(nextRuleIndex: number) {
    setRuleIndex(nextRuleIndex); setPage(0);
  }

  const matched = summary.fully_matched_records ?? Math.max(0, (summary.matched_records ?? 0) - summary.report_rows - summary.confidence_review);
  const total = summary.source_records ?? Math.max(0, matched + summary.report_rows + summary.only_in_file_1 + summary.only_in_file_2 + summary.confidence_review);
  const accuracy = total ? (matched / total) * 100 : 0;
  const chartTotal = Math.max(1, matched + summary.report_rows + summary.only_in_file_1 + summary.only_in_file_2);
  // Identity outcomes are reported separately; jobs from before the split fall back to one review card.
  const hasIdentityBreakdown = summary.exact_matches !== undefined;
  const chartStyle = { background: `conic-gradient(#1d9a6c 0 ${(matched / chartTotal) * 100}%, #ef6f63 ${(matched / chartTotal) * 100}% ${((matched + summary.report_rows) / chartTotal) * 100}%, #f5b54c ${((matched + summary.report_rows) / chartTotal) * 100}% ${((matched + summary.report_rows + summary.only_in_file_1) / chartTotal) * 100}%, #64748b ${((matched + summary.report_rows + summary.only_in_file_1) / chartTotal) * 100}% 100%)` };
  const cards: Array<{ category: PreviewCategory; title: string; label: string; count: number; icon: typeof AlertTriangle; tone: string }> = [
    { category: "discrepancies", title: "Discrepancies", label: "Matched records with mismatched field values", count: summary.report_rows, icon: AlertTriangle, tone: "coral" },
    { category: "only_file_1", title: `Present in ${file1Name} only`, label: `Found only in ${file1Name}`, count: summary.only_in_file_1, icon: FileWarning, tone: "amber" },
    { category: "only_file_2", title: `Present in ${file2Name} only`, label: `Found only in ${file2Name}`, count: summary.only_in_file_2, icon: FileWarning, tone: "slate" },
    ...(hasIdentityBreakdown ? [
      { category: "exception_matches" as const, title: "Matches to confirm", label: "Please confirm: similar key with matching secondary keys, or an amount/date pass", count: summary.exception_matches ?? 0, icon: Split, tone: "violet" },
      { category: "ambiguous_matches" as const, title: "Ambiguous matches", label: "Several candidates qualified, so none was selected", count: summary.ambiguous_matches ?? 0, icon: HelpCircle, tone: "amber" },
      { category: "not_found" as const, title: "Not found", label: "No destination record qualified for this primary key", count: summary.not_found_matches ?? 0, icon: XCircle, tone: "slate" },
    ] : [
      { category: "review" as const, title: "Identity review", label: "Secondary, ambiguous, or unresolved primary-key matches", count: summary.confidence_review, icon: Eye, tone: "violet" },
    ]),
  ];
  const displayedRows = useMemo(() => preview?.rows.filter((row) => Object.values(row).some((value) => String(value ?? "").toLowerCase().includes(query.toLowerCase()))) ?? [], [preview, query]);

  function openPreview(nextCategory: PreviewCategory) {
    setCategory(nextCategory); setPage(0); setQuery(""); setPreviewOpen(true);
  }

  const decisionKey = (row: Record<string, unknown>) => `${row["MATCH KEY"] ?? ""}||${row["CANDIDATE KEY"] ?? ""}`;
  const reviewable = (row: Record<string, unknown>) => row["IDENTITY CLASSIFICATION"] === "EXCEPTION_MATCH" && Boolean(row["MATCH KEY"]) && Boolean(row["CANDIDATE KEY"]);
  const showDecisions = Boolean(preview && ["exception_matches", "review"].includes(preview.category) && preview.rows.some(reviewable));

  async function decide(row: Record<string, unknown>, decision: "accept" | "reject") {
    if (!job) return;
    try {
      const confidence = Number(String(row["MATCH CONFIDENCE"] ?? "").replace("%", ""));
      await api.recordDecision({ value_1: String(row["MATCH KEY"]), value_2: String(row["CANDIDATE KEY"]), decision, job_id: job.id, column_hint: "key", confidence: Number.isFinite(confidence) ? confidence : undefined });
      setDecisions((current) => ({ ...current, [decisionKey(row)]: decision }));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not record the decision");
    }
  }

  async function saveSetup() {
    if (!job) return;
    const name = window.prompt("Name this setup so it can be re-run on new files:", `${file1Name} vs ${file2Name}`);
    if (!name?.trim()) return;
    try {
      await api.createTemplate({ name: name.trim(), job_id: job.id });
      setSavedNote(`Saved as "${name.trim()}". Find it under Saved setups.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not save the setup");
    }
  }

  async function download(pairId?: string) { if (!job) return; try { await api.downloadJobReport(job.id, pairId); } catch (error) { setMessage(error instanceof Error ? error.message : "Download failed"); } }
  const downloadLabel = isMultiPair ? `Download all reports (ZIP, ${filePairs.filter((pair) => pair.report_filename).length})` : "Default Report";
  const pairDownload = isMultiPair && (activeRule ? filePairs.find((pair) => pair.file_pair_id === activeRule.file_pair_id) : activePair);
  // A workbook can be customized when there is one: the job's, or the selected pair's.
  const canCustomize = !isMultiPair || Boolean(pairDownload && pairDownload.report_filename);

  if (!job || !["completed", "completed_with_errors"].includes(job.status)) return <section className="page"><div className="empty-state"><h2>No completed report selected</h2><p>Complete a reconciliation to see its results dashboard.</p><button type="button" className="primary" onClick={onNewReconciliation}>Start reconciliation</button></div></section>;

  if (viewStage === "customize") {
    return (
      <ReportCustomizer
        source1Name={file1Name}
        source2Name={file2Name}
        onGenerate={(config) => {
          setCustomConfig(config);
          setViewStage("download");
        }}
        onCancel={() => setViewStage("results")}
      />
    );
  }

  if (viewStage === "download") {
    return (
      <section className="page results-page">
        <div className="page-title">
          <div>
            <span className="eyebrow">Report Ready</span>
            <h1>Download Your Report</h1>
            <p>Your customized report has been generated successfully.</p>
          </div>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button type="button" className="secondary" onClick={() => setViewStage("customize")}>
              <ArrowLeft size={18} /> Back to Customize
            </button>
          </div>
        </div>
        
        <div className="ready-card" style={{ background: '#fff', border: '1px solid #dbe6e5', borderRadius: '8px' }}>
          <div>
            <CheckCircle2 size={40} color="#087d72" />
          </div>
          <div>
            <h2>Report Generated</h2>
            <p>The detailed Excel workbook contains your customized configuration.</p>
            {message && <p className="error-text" style={{ marginTop: '10px' }}>{message}</p>}
          </div>
          <button type="button" className="primary run-button" onClick={() => {
            if (job && customConfig) {
              api.downloadCustomReport(job.id, customConfig, pairDownload ? pairDownload.file_pair_id : undefined).catch((err) => setMessage(err instanceof Error ? err.message : "Download failed"));
            }
          }}>
            <Download size={20} /> Download Report
          </button>
        </div>
      </section>
    );
  }

  return <section className="page results-page">
    <div className="page-title"><div><span className="eyebrow">Reconciliation complete</span><h1>Results dashboard</h1><p>{isMultiPair ? `${filePairs.length} file pairs were reconciled independently, each with its own workbook.` : "Review exceptions, inspect the records behind them, and download the detailed workbook."}</p></div>
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        {/* Customization rebuilds one workbook: the job's, or the selected file pair's. */}
        {canCustomize && <button type="button" className="secondary" onClick={() => setViewStage("customize")}><Settings2 size={18} />{pairDownload ? "Customize this pair" : "Customize Report"}</button>}
        {pairDownload && pairDownload.report_filename && <button type="button" className="secondary" onClick={() => download(pairDownload.file_pair_id)}><Download size={18} />{pairDownload.report_filename}</button>}
        {job.job_type === "generic" && <button type="button" className="secondary" onClick={saveSetup}><BookmarkPlus size={18} />Save for reuse</button>}
        <button type="button" className="primary" onClick={() => download()}><Download size={18} />{downloadLabel}</button>
      </div>
    </div>
    {savedNote && <p className="success-text"><CheckCircle2 size={16} />{savedNote}</p>}
    {(isMultiPair || (jobSummary.sheet_rules?.length ?? 0) > 1) && <div className="results-scope-bar">
      {isMultiPair && <label><span>File pair</span><select value={filePairId} onChange={(event) => selectFilePair(event.target.value)}><option value="">All file pairs ({filePairs.length})</option>{filePairs.map((pair) => <option key={pair.file_pair_id} value={pair.file_pair_id}>{pair.label}{pair.status === "completed" ? "" : pair.status === "failed" ? " (failed)" : " (with errors)"}</option>)}</select></label>}
      {rulesInScope.length > 1 && <div className="rule-navigator" aria-label="Sheet rule navigation">
        <button type="button" className="icon-button" onClick={() => selectRule(ruleIndex - 1)} disabled={ruleIndex < 0} title="Previous sheet rule"><ArrowLeft size={16} /></button>
        <div><span>{activeRule ? `Rule ${ruleIndex + 1} of ${rulesInScope.length}` : `All ${rulesInScope.length} sheet rules`}</span><strong>{activeRule ? activeRule.report_label : "Aggregate of every rule in scope"}</strong></div>
        <button type="button" className="icon-button" onClick={() => selectRule(ruleIndex + 1)} disabled={ruleIndex >= rulesInScope.length - 1} title="Next sheet rule"><ArrowRight size={16} /></button>
        {activeRule && <button type="button" className="text-command" onClick={() => selectRule(-1)}>Show all rules</button>}
      </div>}
      {activeRule && <p className="rule-scope-detail">Key: {activeRule.primary_key_source.join(" + ")} → {activeRule.primary_key_destination.join(" + ")}{activeRule.secondary_conditions.length ? ` · ${activeRule.secondary_conditions.length} secondary condition${activeRule.secondary_conditions.length === 1 ? "" : "s"}` : ""}{activeRule.date_only_override ? " · date-only override enabled" : ""}</p>}
    </div>}
    {activeRule?.status === "failed" && <p className="error-text"><XCircle size={16} />This sheet rule failed and produced no results: {activeRule.error}</p>}
    <div className="summary-grid"><Metric label="Records compared" value={total} icon={<ShieldCheck size={20} />} tone="blue" /><Metric label="Fully matched" value={matched} icon={<CheckCircle2 size={20} />} tone="green" /><Metric label="Discrepancies found" value={summary.report_rows} icon={<AlertTriangle size={20} />} tone="coral" /><Metric label="Reconciliation accuracy" value={total ? `${accuracy.toFixed(2)}%` : "—"} icon={<ShieldCheck size={20} />} tone="violet" /></div>
    {hasIdentityBreakdown && <div className="identity-breakdown" aria-label="Identity resolution outcomes">
      <IdentityStat label="Exact" value={summary.exact_matches ?? 0} hint="Primary key matched after normalization" />
      <IdentityStat label="To confirm" value={summary.exception_matches ?? 0} hint="Similar key, or matched by an amount/date pass" />
      <IdentityStat label="Ambiguous" value={summary.ambiguous_matches ?? 0} hint="Several candidates; none selected" />
      <IdentityStat label="Field discrepancy" value={summary.field_discrepancies ?? summary.report_rows} hint="Matched, but mapped values differ" />
      <IdentityStat label="Not found" value={summary.not_found_matches ?? 0} hint="No qualifying destination record" />
    </div>}
    <div className="chart-card"><div><h2>Result distribution</h2><p>Matched records and exceptions in this run.</p></div><div className="donut-wrap"><div className="donut" style={chartStyle}><span>{total}</span><small>records</small></div><ul className="chart-legend"><li><i className="legend-green" />Matched <strong>{matched}</strong></li><li><i className="legend-coral" />Discrepancies <strong>{summary.report_rows}</strong></li><li><i className="legend-amber" />{file1Name} only <strong>{summary.only_in_file_1}</strong></li><li><i className="legend-slate" />{file2Name} only <strong>{summary.only_in_file_2}</strong></li></ul></div></div>
    <section><div className="section-heading"><div><h2>Exception categories</h2><p>Open any category to inspect a paginated preview of up to 25 report rows at a time.</p></div></div><div className={`result-card-grid${cards.length > 4 ? " is-three-column" : ""}`}>{cards.map(({ category: cardCategory, title, label, count, icon: Icon, tone }) => <article className={`result-card tone-${tone}`} key={cardCategory}><Icon size={20} /><span>{title}</span><strong>{count.toLocaleString()}</strong><p>{label}</p><button type="button" className="text-command" onClick={() => openPreview(cardCategory)}><Eye size={16} />View details</button></article>)}</div></section>
    {preview && <section className="preview-panel"><div className="section-heading"><div><h2>{cards.find((card) => card.category === preview.category)?.title ?? preview.sheet_name}{activeRule ? ` · ${activeRule.report_label}` : activePair ? ` · ${activePair.label}` : ""}</h2><p>{preview.total_rows.toLocaleString()} rows in the workbook</p></div><label className="search-field"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter visible rows" /></label></div><div className="table-scroll"><table><thead><tr>{showDecisions && <th>Your decision</th>}{preview.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{loadingPreview ? <tr><td colSpan={Math.max(1, preview.columns.length + (showDecisions ? 1 : 0))}>Loading preview...</td></tr> : displayedRows.length ? displayedRows.map((row, index) => <tr key={index}>{showDecisions && <td className="decision-cell">{reviewable(row) ? (decisions[decisionKey(row)] ? <span className={`badge ${decisions[decisionKey(row)] === "accept" ? "success" : "danger"}`}>{decisions[decisionKey(row)] === "accept" ? "Confirmed" : "Rejected"}</span> : <><button type="button" className="icon-button" title="Confirm this match" aria-label="Confirm this match" onClick={() => decide(row, "accept")}><ThumbsUp size={14} /></button><button type="button" className="icon-button" title="Not the same record" aria-label="Not the same record" onClick={() => decide(row, "reject")}><ThumbsDown size={14} /></button></>) : null}</td>}{preview.columns.map((column) => <td key={column}>{row[column] ?? "—"}</td>)}</tr>) : <tr><td colSpan={Math.max(1, preview.columns.length)}>No visible rows match this filter.</td></tr>}</tbody></table></div><div className="pagination"><span>Showing {Math.min(preview.offset + 1, preview.total_rows)}–{Math.min(preview.offset + preview.rows.length, preview.total_rows)} of {preview.total_rows}</span><div><button type="button" className="icon-button" onClick={() => setPage((current) => Math.max(0, current - 1))} disabled={page === 0 || loadingPreview} title="Previous preview page"><ArrowLeft size={16} /></button><button type="button" className="icon-button" onClick={() => setPage((current) => current + 1)} disabled={preview.offset + preview.rows.length >= preview.total_rows || loadingPreview} title="Next preview page"><ArrowRight size={16} /></button></div></div></section>}
    {job.status === "completed_with_errors" && <section className="rule-errors"><h2><AlertTriangle size={18} />Some sheet rules failed</h2><p>Successful sheet rules are included above and in the report. These rules produced no results:</p><ul>{failedRules.map((rule) => <li key={rule.sheet_rule_id}><strong>{rule.report_label}</strong><span>{rule.error}</span></li>)}</ul></section>}
    {message && <p className="error-text">{message}</p>}
  </section>;
}

function IdentityStat({ label, value, hint }: { label: string; value: number; hint: string }) { return <div className="identity-stat" title={hint}><span>{label}</span><strong>{value.toLocaleString()}</strong><small>{hint}</small></div>; }

function Metric({ label, value, icon, tone }: { label: string; value: string | number; icon: React.ReactNode; tone: string }) { return <article className={`summary-card tone-${tone}`}><span className="metric-icon">{icon}</span><span>{label}</span><strong>{typeof value === "number" ? value.toLocaleString() : value}</strong></article>; }
