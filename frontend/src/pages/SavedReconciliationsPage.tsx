import { AlertTriangle, Archive, CheckCircle2, History, Loader2, Play, UploadCloud, Wand2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { PrecheckPanel } from "../components/PrecheckPanel";
import { precheckBlocked, precheckNeedsAcknowledgement, precheckSummary } from "../lib/plan";
import { api } from "../services/api";
import type { GenericPlanPayload, Job, PrecheckResult, TemplateResolution, TemplateSummary, UploadedFile } from "../types";

interface Props {
  onJobCreated: (job: Job) => void;
  onNewReconciliation: () => void;
}

type Overrides = Record<string, Record<string, Record<string, string>>>;

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "Never";
}

export function SavedReconciliationsPage({ onJobCreated, onNewReconciliation }: Props) {
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [active, setActive] = useState<TemplateSummary | null>(null);

  async function load() {
    setLoading(true);
    try {
      setTemplates(await api.listTemplates());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load saved reconciliations");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load().catch(() => undefined); }, []);

  async function archive(template: TemplateSummary) {
    if (!window.confirm(`Archive "${template.name}"? It will no longer be listed; its history stays in the audit log.`)) return;
    try {
      await api.archiveTemplate(template.id);
      if (active?.id === template.id) setActive(null);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not archive");
    }
  }

  return <section className="page">
    <div className="page-title"><div><span className="eyebrow">Reuse</span><h1>Saved reconciliations</h1><p>Run a saved setup on new files in one go. Columns are matched automatically when names change; anything uncertain is shown for you to choose.</p></div>
      <button type="button" className="secondary" onClick={onNewReconciliation}>New setup</button></div>
    {message && <p className="error-text">{message}</p>}
    {loading ? <div className="loading-state"><Loader2 className="animate-spin" /> Loading…</div> : templates.length === 0
      ? <div className="empty-state"><h2>Nothing saved yet</h2><p>Set up a reconciliation and choose "Save for reuse" on the review step, or save a finished run from its results page.</p></div>
      : <div className="template-grid">{templates.map((template) => <article key={template.id} className={`template-card ${active?.id === template.id ? "is-active" : ""}`}>
        <div><h2>{template.name}</h2>{template.description && <p>{template.description}</p>}</div>
        <dl>
          <div><dt>File pairs</dt><dd>{template.summary.file_pairs}</dd></div>
          <div><dt>Sheet rules</dt><dd>{template.summary.sheet_rules}</dd></div>
          <div><dt>Version</dt><dd>{template.current_version}</dd></div>
          <div><dt>Last run</dt><dd>{formatDate(template.last_run_at)}</dd></div>
        </dl>
        {template.summary.keys.length > 0 && <small>Keys: {template.summary.keys.join("; ")}</small>}
        <div className="button-row">
          <button type="button" className="primary" onClick={() => setActive(template)}><Play size={15} />Run on new files</button>
          <button type="button" className="icon-button" onClick={() => archive(template)} title="Archive" aria-label={`Archive ${template.name}`}><Archive size={16} /></button>
        </div>
      </article>)}</div>}
    {active && <TemplateRunner key={active.id} template={active} onJobCreated={onJobCreated} onClose={() => setActive(null)} />}
  </section>;
}

function TemplateRunner({ template, onJobCreated, onClose }: { template: TemplateSummary; onJobCreated: (job: Job) => void; onClose: () => void }) {
  const [files, setFiles] = useState<Record<string, { source?: UploadedFile; destination?: UploadedFile }>>({});
  const [uploading, setUploading] = useState("");
  const [resolution, setResolution] = useState<TemplateResolution | null>(null);
  const [plan, setPlan] = useState<GenericPlanPayload | null>(null);
  const [sheetOverrides, setSheetOverrides] = useState<Overrides>({});
  const [columnOverrides, setColumnOverrides] = useState<Overrides>({});
  const [precheck, setPrecheck] = useState<PrecheckResult | null>(null);
  const [precheckLoading, setPrecheckLoading] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [versions, setVersions] = useState<TemplateSummary["versions"]>([]);

  useEffect(() => { api.getTemplate(template.id).then((detail) => setVersions(detail.versions ?? [])).catch(() => undefined); }, [template.id]);

  const assignments = template.summary.pairs.map((pair) => ({ pair, chosen: files[pair.file_pair_id] ?? {} }));
  const allChosen = assignments.every(({ chosen }) => chosen.source && chosen.destination);
  const request = () => ({
    files: assignments.map(({ pair, chosen }) => ({ file_pair_id: pair.file_pair_id, source_file_id: chosen.source!.id, destination_file_id: chosen.destination!.id })),
    sheet_overrides: sheetOverrides,
    column_overrides: columnOverrides,
  });

  async function upload(pairId: string, side: "source" | "destination", file: File) {
    setUploading(`${pairId}-${side}`);
    setMessage("");
    try {
      const stored = await api.uploadFile(file);
      setFiles((current) => ({ ...current, [pairId]: { ...current[pairId], [side]: stored } }));
      setResolution(null); setPlan(null); setPrecheck(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setUploading("");
    }
  }

  async function resolve() {
    setBusy(true); setMessage(""); setPrecheck(null);
    try {
      const result = await api.resolveTemplate(template.id, request());
      setResolution(result.resolution);
      setPlan(result.plan);
      if (result.plan) {
        setPrecheckLoading(true);
        setAcknowledged(false);
        setPrecheck(await api.precheck(result.plan));
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not match the files");
    } finally {
      setBusy(false); setPrecheckLoading(false);
    }
  }

  async function run() {
    setBusy(true); setMessage("");
    try {
      const job = await api.runTemplate(template.id, {
        ...request(),
        precheck_acknowledged: Boolean(precheck && (precheck.summary.warning === 0 || acknowledged)),
        precheck_summary: precheck ? precheckSummary(precheck) : {},
      });
      onJobCreated(job);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not start the run");
    } finally {
      setBusy(false);
    }
  }

  function setOverride(kind: "sheet" | "column", scope: string, side: string, name: string, value: string) {
    const setter = kind === "sheet" ? setSheetOverrides : setColumnOverrides;
    setter((current) => ({ ...current, [scope]: { ...current[scope], [side]: { ...current[scope]?.[side], [name]: value } } }));
    setPlan(null); setPrecheck(null);
  }

  const canRun = Boolean(plan) && !precheckLoading && !precheckBlocked(precheck) && (!precheckNeedsAcknowledgement(precheck) || acknowledged);

  return <section className="template-runner">
    <div className="section-heading"><div><h2>Run "{template.name}"</h2><p>Choose this period's files for each file pair. The saved keys, rules, passes and report settings (version {template.current_version}) are applied.</p></div>
      <button type="button" className="secondary" onClick={onClose}>Close</button></div>
    {assignments.map(({ pair, chosen }) => <div className="template-pair" key={pair.file_pair_id}>
      <h3>{pair.label || pair.file_pair_id}</h3>
      <div className="template-pair-files">
        {(["source", "destination"] as const).map((side) => <FilePick key={side} label={side === "source" ? "Source" : "Destination"} hint={side === "source" ? pair.source_filename : pair.destination_filename} file={chosen[side]} busy={uploading === `${pair.file_pair_id}-${side}`} onFile={(file) => upload(pair.file_pair_id, side, file)} />)}
      </div>
    </div>)}
    <button type="button" className="secondary" onClick={resolve} disabled={!allChosen || busy}><Wand2 size={15} />{resolution ? "Match columns again" : "Match sheets and columns"}</button>

    {resolution && <div className="resolution">
      {resolution.unresolved.length > 0 && <div className="resolution-block is-warning">
        <h3><AlertTriangle size={16} />Choose these yourself</h3>
        <p>These could not be matched with certainty, so nothing was guessed.</p>
        {resolution.unresolved.map((item, index) => {
          const scope = item.sheet ? item.file_pair_id : item.sheet_rule_id ?? "";
          const name = item.sheet ?? item.column ?? "";
          const kind = item.sheet ? "sheet" : "column";
          const current = (kind === "sheet" ? sheetOverrides : columnOverrides)[scope]?.[item.side ?? ""]?.[name] ?? "";
          return <label key={`${scope}-${name}-${index}`} className="resolution-row">
            <span>{item.side === "source" ? "Source" : "Destination"} {kind} <strong>{name}</strong></span>
            <select value={current} onChange={(event) => setOverride(kind, scope, item.side ?? "", name, event.target.value)}>
              <option value="">Choose…</option>
              {(item.available ?? []).map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
          </label>;
        })}
      </div>}
      {(resolution.automatic?.length ?? 0) > 0 && <div className="resolution-block">
        <h3><CheckCircle2 size={16} />Matched automatically</h3>
        <ul>{resolution.automatic!.map((item, index) => <li key={index}>{item.side ? `${item.side === "source" ? "Source" : "Destination"}: ` : ""}<strong>{item.template ?? "(first sheet)"}</strong> → {item.resolved} <small>({item.method})</small></li>)}</ul>
      </div>}
    </div>}

    {plan && <PrecheckPanel result={precheck} loading={precheckLoading} error="" acknowledged={acknowledged} onAcknowledge={setAcknowledged} onRerun={resolve} />}
    {message && <p className="error-text">{message}</p>}
    <button type="button" className="primary run-button" onClick={run} disabled={!canRun || busy}><Play size={18} />{busy ? "Working…" : "Run reconciliation"}</button>

    {versions && versions.length > 0 && <details className="rule-advanced"><summary><History size={14} /> Version history ({versions.length})</summary>
      <ul className="version-list">{versions.map((version) => <li key={version.version}><strong>v{version.version}</strong> {version.change_note} <small>{formatDate(version.created_at)}</small></li>)}</ul>
    </details>}
  </section>;
}

function FilePick({ label, hint, file, busy, onFile }: { label: string; hint: string; file?: UploadedFile; busy: boolean; onFile: (file: File) => void }) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  return <div className={`file-pick ${file ? "is-ready" : ""}`}>
    <span>{label}</span>
    <strong>{file ? file.original_filename : "No file chosen"}</strong>
    {hint && <small>Last time: {hint}</small>}
    <button type="button" className="secondary" onClick={() => inputRef.current?.click()} disabled={busy}>{busy ? <Loader2 size={15} className="animate-spin" /> : <UploadCloud size={15} />}{file ? "Replace" : "Choose file"}</button>
    <input ref={inputRef} className="visually-hidden" type="file" accept=".xlsx,.xls,.csv,.tsv,.txt,.pdf,.docx" onChange={(event) => { const chosen = event.target.files?.item(0); if (chosen) onFile(chosen); event.currentTarget.value = ""; }} />
  </div>;
}
