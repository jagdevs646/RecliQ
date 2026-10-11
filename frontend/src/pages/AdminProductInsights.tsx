import { useEffect, useRef, useState, type ReactNode } from "react";
import { Activity, BookMarked, Brain, RefreshCw, Tags, Wand2 } from "lucide-react";
import { api } from "../services/api";

type Insights = {
  period: "today" | "7d" | "30d" | "all";
  saved_setups: { total: number; workspaces: number };
  aliases: { active: number; workspaces: number };
  auto_resolution: { active_rules: number; workspaces: number; observed_exception_occurrences: number; workspaces_with_patterns: number };
  learning: { confirmed_pairings: number; rejected_pairings: number; workspaces: number };
};

export function AdminProductInsights() {
  const [data, setData] = useState<Insights | null>(null);
  const [period, setPeriod] = useState<Insights["period"]>("all");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const requestSequence = useRef(0);

  async function load() {
    const requestId = ++requestSequence.current;
    setLoading(true); setError(""); setData(null);
    try { const result = await api.adminProductInsights(period) as Insights; if (requestSequence.current === requestId) setData(result); }
    catch (caught) { if (requestSequence.current === requestId) setError(caught instanceof Error ? caught.message : "Product insights could not be loaded."); }
    finally { if (requestSequence.current === requestId) setLoading(false); }
  }
  useEffect(() => { void load(); }, [period]);

  return <section className="admin-section insights-section">
    <div className="section-heading"><div><span className="eyebrow">Aggregate usage</span><h2>Product insights</h2><p>Adoption metadata by creation or last-observed date. Customer documents and reconciliation values are not included.</p></div><button className="icon-button" type="button" aria-label="Refresh product insights" title="Refresh" disabled={loading} onClick={() => void load()}><RefreshCw size={16} /></button></div>
    <div className="admin-insight-filters"><div className="segmented-control" role="group" aria-label="Product insights period">{([["today", "Today"], ["7d", "7 days"], ["30d", "30 days"], ["all", "All time"]] as const).map(([value, label]) => <button key={value} type="button" className={period === value ? "is-active" : ""} aria-pressed={period === value} onClick={() => setPeriod(value)}>{label}</button>)}</div>{loading && <span className="muted" role="status">Updating {period === "all" ? "all-time" : period === "today" ? "today's" : `last ${period === "7d" ? "7" : "30"} days'`} totals…</span>}</div>
    {error && <div className="support-inline-error" role="alert"><span>{error}</span><button type="button" className="secondary compact" onClick={() => void load()}>Retry</button></div>}
    {loading ? <div className="admin-insights-state" role="status">Loading aggregate product data…</div> : data && <div className="admin-insights-grid">
      <Insight icon={<BookMarked />} title="Saved setups" main={data.saved_setups.total} label="active setups" detail={`${data.saved_setups.workspaces.toLocaleString()} workspaces have at least one active setup.`} />
      <Insight icon={<Tags />} title="Name aliases" main={data.aliases.active} label="active aliases" detail={`${data.aliases.workspaces.toLocaleString()} workspaces use aliases. Aliases remain private to each workspace.`} />
      <Insight icon={<Wand2 />} title="Auto-resolution" main={data.auto_resolution.active_rules} label="enabled rules" detail={`${data.auto_resolution.workspaces.toLocaleString()} workspaces have rules; ${data.auto_resolution.observed_exception_occurrences.toLocaleString()} exception observations are recorded.`} />
      <Insight icon={<Brain />} title="Learning" main={data.learning.confirmed_pairings} label="confirmed pairings" detail={`${data.learning.rejected_pairings.toLocaleString()} rejected pairings across ${data.learning.workspaces.toLocaleString()} workspaces. These are recorded review decisions, not a claimed accuracy score.`} />
    </div>}
    <p className="metric-definition"><Activity size={14} aria-hidden="true" /> Counts are current totals supported by stored metadata; historical adoption trends are not available for every feature.</p>
  </section>;
}

function Insight({ icon, title, main, label, detail }: { icon: ReactNode; title: string; main: number; label: string; detail: string }) {
  return <article className="admin-insight-row"><span className="admin-insight-icon">{icon}</span><div><h3>{title}</h3><p>{detail}</p></div><strong>{main.toLocaleString()}<small>{label}</small></strong></article>;
}
