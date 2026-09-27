import { CheckCircle2, Loader2, Plus, Trash2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../services/api";
import type { AliasSuggestion, EntityAlias } from "../types";

/** Organization aliases: added by people, suggested from repeated review decisions, never learned automatically. */
export function AliasesPage() {
  const [aliases, setAliases] = useState<EntityAlias[]>([]);
  const [suggestions, setSuggestions] = useState<AliasSuggestion[]>([]);
  const [minimum, setMinimum] = useState(2);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [canonical, setCanonical] = useState("");
  const [variants, setVariants] = useState("");
  const [testA, setTestA] = useState("ABC Pvt Ltd");
  const [testB, setTestB] = useState("A.B.C. PRIVATE LIMITED");
  const [test, setTest] = useState<{ equivalent: boolean; rules: string[]; normalized: [string, string] } | null>(null);

  async function load() {
    setLoading(true);
    try {
      const [aliasList, suggestionList] = await Promise.all([api.listAliases(), api.aliasSuggestions()]);
      setAliases(aliasList);
      setSuggestions(suggestionList.suggestions);
      setMinimum(suggestionList.minimum_acceptances);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load aliases");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load().catch(() => undefined); }, []);

  async function add() {
    const list = variants.split(/\n|;/).map((value) => value.trim()).filter(Boolean);
    if (!canonical.trim() || !list.length) {
      setMessage("Enter the name and at least one other spelling.");
      return;
    }
    try {
      await api.createAlias(canonical.trim(), list);
      setCanonical(""); setVariants(""); setMessage("");
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not save the alias");
    }
  }

  async function approve(suggestion: AliasSuggestion, useFirstAsName: boolean) {
    const [name, variant] = useFirstAsName ? [suggestion.value_1, suggestion.value_2] : [suggestion.value_2, suggestion.value_1];
    try {
      await api.approveSuggestion(name, variant, suggestion.column_hint);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not approve");
    }
  }

  async function dismiss(suggestion: AliasSuggestion) {
    // A rejection permanently stops this pair from being suggested.
    try {
      await api.recordDecision({ value_1: suggestion.value_1, value_2: suggestion.value_2, decision: "reject", column_hint: suggestion.column_hint, note: "Dismissed suggestion" });
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not dismiss");
    }
  }

  async function remove(alias: EntityAlias) {
    try {
      await api.removeAlias(alias.id);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not remove");
    }
  }

  async function runTest() {
    try {
      const result = await api.previewNormalization(testA, testB);
      setTest({ equivalent: result.equivalent, rules: result.rules, normalized: [result.value_1.normalized, result.value_2.normalized] });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not test");
    }
  }

  return <section className="page">
    <div className="page-title"><div><span className="eyebrow">Matching intelligence</span><h1>Name aliases</h1><p>RecliQ already treats "ABC Pvt Ltd" and "ABC PRIVATE LIMITED" as the same name. Add aliases for names it cannot know, such as "IBM" = "International Business Machines". Only aliases you approve are used.</p></div></div>
    {message && <p className="error-text">{message}</p>}

    <div className="alias-layout">
      <section className="panel-card">
        <h2>Test two names</h2>
        <div className="alias-test">
          <input value={testA} onChange={(event) => setTestA(event.target.value)} aria-label="First name" />
          <input value={testB} onChange={(event) => setTestB(event.target.value)} aria-label="Second name" />
          <button type="button" className="secondary" onClick={runTest}>Compare</button>
        </div>
        {test && <p className={test.equivalent ? "success-text" : "error-text"}>{test.equivalent ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
          {test.equivalent ? `Treated as the same name${test.rules.length ? ` (${test.rules.join(", ")})` : ""}.` : `Different names ("${test.normalized[0]}" vs "${test.normalized[1]}"). Add an alias if they are the same business.`}</p>}
      </section>

      <section className="panel-card">
        <h2>Add an alias</h2>
        <label className="stacked-field"><span>Name</span><input value={canonical} onChange={(event) => setCanonical(event.target.value)} placeholder="International Business Machines" /></label>
        <label className="stacked-field"><span>Other spellings (one per line)</span><textarea value={variants} onChange={(event) => setVariants(event.target.value)} rows={3} placeholder={"IBM\nI.B.M. India"} /></label>
        <button type="button" className="primary" onClick={add}><Plus size={15} />Save alias</button>
      </section>
    </div>

    <section className="panel-card">
      <h2>Suggested from your reviews</h2>
      <p className="muted">A pair is suggested after it was confirmed in at least {minimum} different reconciliations and never rejected. It is used only after you approve it.</p>
      {loading ? <Loader2 className="animate-spin" /> : suggestions.length === 0 ? <p className="rule-empty">No suggestions yet.</p> : <ul className="suggestion-list">{suggestions.map((suggestion) => <li key={suggestion.pair_key}>
        <span><strong>{suggestion.value_1}</strong> = <strong>{suggestion.value_2}</strong><small>Confirmed {suggestion.acceptances} times in {suggestion.jobs} reconciliations</small></span>
        <button type="button" className="secondary" onClick={() => approve(suggestion, true)}>Approve (name: {suggestion.value_1})</button>
        <button type="button" className="secondary" onClick={() => approve(suggestion, false)}>Approve (name: {suggestion.value_2})</button>
        <button type="button" className="text-command" onClick={() => dismiss(suggestion)}>Not the same</button>
      </li>)}</ul>}
    </section>

    <section className="panel-card">
      <h2>Active aliases ({aliases.length})</h2>
      {aliases.length === 0 ? <p className="rule-empty">No aliases yet.</p> : <div className="table-scroll"><table><thead><tr><th>Name</th><th>Also written as</th><th>Added</th><th /></tr></thead><tbody>{aliases.map((alias) => <tr key={alias.id}><td>{alias.canonical}</td><td>{alias.variant}</td><td>{alias.source === "suggestion" ? "Approved suggestion" : "Added manually"}</td><td><button type="button" className="icon-button" onClick={() => remove(alias)} title="Remove alias" aria-label={`Remove alias ${alias.variant}`}><Trash2 size={15} /></button></td></tr>)}</tbody></table></div>}
    </section>
  </section>;
}
