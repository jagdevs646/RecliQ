import { ArrowDown, ArrowUp, Eye, Lightbulb, Loader2, Pencil, Plus, Save, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { COLUMN_GROUPS, EMPTY_RULE, NO_VALUE_OPERATORS, NUMBER_VALUE_OPERATORS, cleanDraft, defaultConditions } from "../lib/rules";
import { api } from "../services/api";
import type { Job, ResolutionRule, RuleCatalog, RuleCategory, RuleCondition, RuleDraft, RulePreviewResult, RuleSuggestion } from "../types";

/** Auto-resolution rules for recurring exceptions: written by people, tested on a finished run, applied in order. */
export function ResolutionRulesPage() {
  const [catalog, setCatalog] = useState<RuleCatalog | null>(null);
  const [rules, setRules] = useState<ResolutionRule[]>([]);
  const [suggestions, setSuggestions] = useState<RuleSuggestion[]>([]);
  const [minimumRuns, setMinimumRuns] = useState(3);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [draft, setDraft] = useState<RuleDraft | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [previewJob, setPreviewJob] = useState("");
  const [previewResult, setPreviewResult] = useState<RulePreviewResult | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const [catalogResult, ruleList, suggestionResult, jobList] = await Promise.all([
        catalog ? Promise.resolve(catalog) : api.ruleCatalog(), api.listRules(), api.ruleSuggestions(), api.listJobs()
      ]);
      setCatalog(catalogResult);
      setRules(ruleList);
      setSuggestions(suggestionResult.suggestions);
      setMinimumRuns(suggestionResult.minimum_reconciliations);
      const finished = jobList.filter((job) => job.job_type === "generic" && ["completed", "completed_with_errors"].includes(job.status));
      setJobs(finished);
      setPreviewJob((current) => current || finished[0]?.id || "");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load rules");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load().catch(() => undefined); }, []);

  const operators = useMemo(() => (catalog?.operators ?? []).filter((item) => draft && item.categories.includes(draft.category)), [catalog, draft?.category]);
  const operatorGroup = (id: string) => catalog?.operators.find((item) => item.id === id)?.group ?? "text";

  function edit(rule: ResolutionRule | null, template?: RuleDraft) {
    setMessage("");
    setPreviewResult(null);
    setEditingId(rule?.id ?? null);
    const source = rule ?? template ?? EMPTY_RULE;
    setDraft({ name: source.name, description: source.description, category: source.category, conditions: source.conditions.map((condition) => ({ ...condition })), action: { ...source.action } });
  }

  function updateCondition(index: number, patch: Partial<RuleCondition>) {
    setDraft((current) => current && { ...current, conditions: current.conditions.map((condition, position) => (position === index ? { ...condition, ...patch } : condition)) });
    setPreviewResult(null);
  }

  async function save() {
    if (!draft) return;
    setBusy(true);
    try {
      if (editingId) await api.updateRule(editingId, cleanDraft(draft));
      else await api.createRule(cleanDraft(draft));
      setDraft(null);
      setEditingId(null);
      setMessage("");
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not save the rule");
    } finally {
      setBusy(false);
    }
  }

  async function runPreview() {
    if (!draft || !previewJob) return;
    setBusy(true);
    try {
      setPreviewResult(await api.previewRule(previewJob, cleanDraft(draft)));
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not test the rule");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(rule: ResolutionRule) {
    try {
      await api.updateRule(rule.id, { enabled: !rule.enabled });
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not change the rule");
    }
  }

  async function remove(rule: ResolutionRule) {
    if (!window.confirm(`Remove the rule "${rule.name}"? Past reports keep showing what it resolved.`)) return;
    try {
      await api.archiveRule(rule.id);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not remove the rule");
    }
  }

  async function move(index: number, offset: number) {
    const order = rules.map((rule) => rule.id);
    const target = index + offset;
    if (target < 0 || target >= order.length) return;
    [order[index], order[target]] = [order[target], order[index]];
    try {
      setRules(await api.reorderRules(order));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not change the order");
    }
  }

  const previewColumns = previewResult?.sample.length ? Object.keys(previewResult.sample[0]).slice(0, 8) : [];

  return <section className="page">
    <div className="page-title"><div><span className="eyebrow">Controls</span><h1>Auto-resolution rules</h1><p>Clear recurring exceptions, such as bank charges or rounding, the same way every period. Resolved items move to their own report tab; nothing is deleted.</p></div>
      <button type="button" className="primary" onClick={() => edit(null)}><Plus size={16} />New rule</button></div>
    {message && <p className="error-text">{message}</p>}

    {draft && catalog && <section className="panel-card rule-editor" aria-label="Rule editor">
      <div className="rule-editor-head"><h2>{editingId ? "Edit rule" : "New rule"}</h2><button type="button" className="icon-button" onClick={() => { setDraft(null); setPreviewResult(null); }} title="Close" aria-label="Close editor"><X size={16} /></button></div>
      <div className="rule-editor-grid">
        <label className="stacked-field"><span>Name</span><input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="Bank charges" /></label>
        <label className="stacked-field"><span>Exception type</span><select value={draft.category} onChange={(event) => { const category = event.target.value as RuleCategory; setDraft({ ...draft, category, conditions: defaultConditions(category) }); setPreviewResult(null); }}>
          {catalog.categories.map((category) => <option key={category.id} value={category.id}>{category.label}</option>)}
        </select></label>
      </div>

      <h3>When all of these are true</h3>
      <div className="condition-list">
        {draft.conditions.map((condition, index) => {
          const group = operatorGroup(condition.operator);
          const numeric = NUMBER_VALUE_OPERATORS.has(condition.operator);
          return <div className="condition-row" key={index}>
            {COLUMN_GROUPS.has(group) ? <input aria-label="Column" value={condition.column ?? ""} onChange={(event) => updateCondition(index, { column: event.target.value })} placeholder="Column, e.g. Narrative (* = any)" /> : <span className="condition-subject">{group === "difference" ? "The difference" : "The match"}</span>}
            <select aria-label="Condition" value={condition.operator} onChange={(event) => updateCondition(index, { operator: event.target.value, value: NUMBER_VALUE_OPERATORS.has(event.target.value) ? 0 : "", value_2: undefined })}>
              {operators.map((operator) => <option key={operator.id} value={operator.id}>{operator.label}</option>)}
            </select>
            {!NO_VALUE_OPERATORS.has(condition.operator) && <input aria-label="Value" type={numeric ? "number" : "text"} step="any" value={condition.value ?? ""} onChange={(event) => updateCondition(index, { value: event.target.value })} placeholder={condition.operator === "field_is" ? "Field, e.g. AMOUNT" : condition.operator === "method_is" ? "e.g. Amount + date" : ""} />}
            {condition.operator === "between" && <input aria-label="Upper value" type="number" step="any" value={condition.value_2 ?? ""} onChange={(event) => updateCondition(index, { value_2: event.target.value })} />}
            <button type="button" className="icon-button" disabled={draft.conditions.length === 1} onClick={() => setDraft({ ...draft, conditions: draft.conditions.filter((_, position) => position !== index) })} title="Remove condition" aria-label="Remove condition"><Trash2 size={14} /></button>
          </div>;
        })}
        <button type="button" className="text-command" onClick={() => setDraft({ ...draft, conditions: [...draft.conditions, { column: "", operator: operators.find((item) => COLUMN_GROUPS.has(item.group))?.id ?? "contains", value: "" }] })}><Plus size={14} />Add condition</button>
      </div>

      <h3>Then resolve it as</h3>
      <div className="rule-editor-grid">
        <label className="stacked-field"><span>Resolution</span><input value={draft.action.resolution} onChange={(event) => setDraft({ ...draft, action: { ...draft.action, resolution: event.target.value } })} placeholder="Bank charges" /></label>
        <label className="stacked-field"><span>GL account</span><input value={draft.action.gl_account ?? ""} onChange={(event) => setDraft({ ...draft, action: { ...draft.action, gl_account: event.target.value } })} placeholder="6100" /></label>
        <label className="stacked-field"><span>Reason code</span><input value={draft.action.reason_code ?? ""} onChange={(event) => setDraft({ ...draft, action: { ...draft.action, reason_code: event.target.value } })} placeholder="BCHG" /></label>
        <label className="stacked-field"><span>Note</span><input value={draft.action.note ?? ""} onChange={(event) => setDraft({ ...draft, action: { ...draft.action, note: event.target.value } })} placeholder="Posted monthly by finance" /></label>
      </div>

      <div className="rule-test">
        <label className="stacked-field"><span>Test on a finished reconciliation</span><select value={previewJob} onChange={(event) => { setPreviewJob(event.target.value); setPreviewResult(null); }}>
          {jobs.length === 0 && <option value="">No finished reconciliations yet</option>}
          {jobs.map((job) => <option key={job.id} value={job.id}>{job.input_file_1_name ?? "File 1"} vs {job.input_file_2_name ?? "File 2"} · {new Date(job.created_at).toLocaleString()}</option>)}
        </select></label>
        <button type="button" className="secondary" onClick={runPreview} disabled={!previewJob || busy}><Eye size={15} />Test rule</button>
        <button type="button" className="primary" onClick={save} disabled={busy}>{busy ? <Loader2 className="animate-spin" size={15} /> : <Save size={15} />}{editingId ? "Save as new version" : "Save rule"}</button>
      </div>
      {previewResult && <div className="rule-preview">
        <p><strong>{previewResult.matches.toLocaleString()}</strong> exception{previewResult.matches === 1 ? "" : "s"} in that run would be resolved. <span className="muted">{previewResult.summary}</span></p>
        <p className="muted">The test uses the columns in that run's report; a column that was not part of the setup is only checked when the rule runs on the full file.</p>
        {previewColumns.length > 0 && <div className="table-scroll"><table><thead><tr>{previewColumns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{previewResult.sample.map((row, index) => <tr key={index}>{previewColumns.map((column) => <td key={column}>{String(row[column] ?? "—")}</td>)}</tr>)}</tbody></table></div>}
      </div>}
    </section>}

    <section className="panel-card">
      <h2>Rules in order ({rules.length})</h2>
      <p className="muted">They run after matching, top to bottom; the first rule that fits an exception resolves it. Every change is in the audit log.</p>
      {loading ? <Loader2 className="animate-spin" /> : rules.length === 0 ? <p className="rule-empty">No rules yet. Every exception is left for a person to review.</p> : <ol className="rule-list">{rules.map((rule, index) => <li key={rule.id} className={rule.enabled ? "" : "is-disabled"}>
        <div className="rule-order"><button type="button" className="icon-button" disabled={index === 0} onClick={() => move(index, -1)} title="Move up" aria-label={`Move ${rule.name} up`}><ArrowUp size={14} /></button><button type="button" className="icon-button" disabled={index === rules.length - 1} onClick={() => move(index, 1)} title="Move down" aria-label={`Move ${rule.name} down`}><ArrowDown size={14} /></button></div>
        <div className="rule-body"><strong>{rule.name} <small>v{rule.version}{rule.action.gl_account ? ` · GL ${rule.action.gl_account}` : ""}{rule.action.reason_code ? ` · ${rule.action.reason_code}` : ""}</small></strong><span>{rule.summary}</span></div>
        <label className="toggle-inline"><input type="checkbox" checked={rule.enabled} onChange={() => toggle(rule)} />{rule.enabled ? "On" : "Off"}</label>
        <button type="button" className="icon-button" onClick={() => edit(rule)} title="Edit rule" aria-label={`Edit ${rule.name}`}><Pencil size={14} /></button>
        <button type="button" className="icon-button" onClick={() => remove(rule)} title="Remove rule" aria-label={`Remove ${rule.name}`}><Trash2 size={14} /></button>
      </li>)}</ol>}
    </section>

    <section className="panel-card">
      <h2><Lightbulb size={17} /> Suggested rules</h2>
      <p className="muted">From exceptions that keep coming back (seen in at least {minimumRuns} reconciliations) and from your reviewers' decisions. Nothing is applied until you save a rule.</p>
      {loading ? <Loader2 className="animate-spin" /> : suggestions.length === 0 ? <p className="rule-empty">No suggestions yet.</p> : <ul className="suggestion-list">{suggestions.map((suggestion, index) => <li key={index}>
        <span><strong>{suggestion.title}</strong><small>{suggestion.detail}</small></span>
        {suggestion.rule && <button type="button" className="secondary" onClick={() => edit(null, suggestion.rule ?? undefined)}><Pencil size={14} />Review as a rule</button>}
      </li>)}</ul>}
    </section>
  </section>;
}
