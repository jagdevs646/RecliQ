import { Brain, Loader2, RotateCcw, Tags, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../services/api";
import type { LearningOverview, Page } from "../types";

const percent = (value: number) => `${Math.round(value * 100)}%`;

/** What reviewers' confirm/reject decisions have taught RecliQ, with the evidence behind each number. */
export function LearningPage({ onNavigate }: { onNavigate: (page: Page) => void }) {
  const [overview, setOverview] = useState<LearningOverview | null>(null);
  const [message, setMessage] = useState("");

  async function load() {
    try {
      setOverview(await api.learningOverview());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load what RecliQ has learned");
    }
  }

  useEffect(() => { load().catch(() => undefined); }, []);

  async function forget(value1: string, value2: string) {
    if (!window.confirm(`Withdraw the rejection of "${value1}" ↔ "${value2}"? RecliQ may propose this pairing again (still for a person to confirm).`)) return;
    try {
      await api.recordDecision({ value_1: value1, value_2: value2, decision: "reset", note: "Rejection withdrawn on the Learning page" });
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not withdraw the rejection");
    }
  }

  if (!overview) return <section className="page">{message ? <p className="error-text">{message}</p> : <Loader2 className="animate-spin" />}</section>;

  return <section className="page">
    <div className="page-title"><div><span className="eyebrow">Matching intelligence</span><h1>Learning from reviews</h1><p>What reviewers' confirmations and rejections have taught RecliQ. A rejected pairing is never proposed again; nothing is matched automatically without an alias or rule you approve.</p></div></div>
    {message && <p className="error-text">{message}</p>}

    <div className="summary-grid learning-stats">
      <div className="summary-card tone-green"><span className="metric-icon"><Brain size={20} /></span><span>Pairings confirmed by reviewers</span><strong>{overview.confirmed_pairs}</strong></div>
      <div className="summary-card tone-coral"><span className="metric-icon"><RotateCcw size={20} /></span><span>Rejected pairings remembered</span><strong>{overview.remembered_rejections.length}</strong></div>
      <button type="button" className="summary-card tone-blue metric-link" onClick={() => onNavigate("aliases")}><span className="metric-icon"><Tags size={20} /></span><span>Name aliases waiting for approval</span><strong>{overview.alias_suggestions}</strong></button>
    </div>

    <section className="panel-card">
      <h2>How reliable each matching method has been</h2>
      <p className="muted">Learned from reviewers' decisions on "Matches to confirm". The conservative estimate is the lower end of a 95% range, so a handful of decisions never looks certain. A method needs {overview.minimum_evidence} decisions before its learned confidence is shown in results.</p>
      {overview.method_weights.length === 0 ? <p className="rule-empty">No decisions yet. Confirm or reject matches on a results page to start.</p> : <div className="table-scroll"><table><thead><tr><th>Found by</th><th>Score</th><th>Confirmed</th><th>Rejected</th><th>Confirmed share</th><th>Conservative estimate</th></tr></thead><tbody>{overview.method_weights.map((row) => <tr key={`${row.method}|${row.band}`}>
        <td>{row.method}</td><td>{row.band === "all" ? "—" : row.band}</td><td>{row.accepted}</td><td>{row.rejected}</td>
        <td>{percent(row.acceptance_rate)}</td>
        <td>{row.enough_evidence ? percent(row.lower_bound) : <span className="muted">Needs {overview.minimum_evidence - row.total} more</span>}</td>
      </tr>)}</tbody></table></div>}
    </section>

    <section className="panel-card">
      <h2>What the evidence suggests</h2>
      {overview.suggestions.length === 0 ? <p className="rule-empty">Nothing yet. Suggestions appear once there is enough evidence.</p> : <ul className="suggestion-list">{overview.suggestions.map((suggestion, index) => <li key={index}>
        <span><strong>{suggestion.title}</strong><small>{suggestion.detail}</small></span>
        {suggestion.rule && <button type="button" className="secondary" onClick={() => onNavigate("rules")}><Wand2 size={14} />Review in Auto-resolution</button>}
      </li>)}</ul>}
    </section>

    <section className="panel-card">
      <h2>Rejected pairings ({overview.remembered_rejections.length})</h2>
      <p className="muted">These are never proposed again. If one was rejected by mistake, withdraw it here; the withdrawal is recorded in the audit log.</p>
      {overview.remembered_rejections.length === 0 ? <p className="rule-empty">No rejected pairings.</p> : <div className="table-scroll"><table><thead><tr><th>Record</th><th>Rejected partner</th><th>Found by</th><th>Rejected on</th><th /></tr></thead><tbody>{overview.remembered_rejections.map((row) => <tr key={`${row.value_1}|${row.value_2}`}>
        <td>{row.value_1}</td><td>{row.value_2}</td><td>{row.method ?? "—"}</td><td>{row.rejected_on || "—"}</td>
        <td><button type="button" className="text-command" onClick={() => forget(row.value_1, row.value_2)}><RotateCcw size={14} />Withdraw</button></td>
      </tr>)}</tbody></table></div>}
    </section>
  </section>;
}
