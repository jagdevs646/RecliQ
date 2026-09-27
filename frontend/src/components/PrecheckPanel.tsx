import { AlertTriangle, CheckCircle2, Info, Loader2, RefreshCw, XCircle } from "lucide-react";
import type { DateFormat, PrecheckIssue, PrecheckResult } from "../types";
import { issuesBySeverity } from "../lib/plan";

interface Props {
  result: PrecheckResult | null;
  loading: boolean;
  error: string;
  acknowledged: boolean;
  onAcknowledge: (value: boolean) => void;
  onRerun: () => void;
  onApplyDateFormat?: (sheetRuleId: string, format: DateFormat) => void;
}

const ICONS = { blocker: XCircle, warning: AlertTriangle, info: Info };
const TITLES = { blocker: "Must be fixed", warning: "Please check", info: "For information" };

function IssueRow({ issue, onFix }: { issue: PrecheckIssue; onFix?: () => void }) {
  const Icon = ICONS[issue.severity];
  const examples = issue.examples.filter((example) => example.value !== null || example.row !== null).slice(0, 3);
  return <li className={`precheck-issue is-${issue.severity}`}>
    <Icon size={16} />
    <div>
      <p>{issue.message}</p>
      {examples.length > 0 && <small>Examples: {examples.map((example) => `${example.row ? `row ${example.row}: ` : ""}${example.value ?? "(blank)"}`).join(" · ")}</small>}
    </div>
    {onFix && <button type="button" className="secondary" onClick={onFix}>Switch the date format</button>}
  </li>;
}

/** Data-quality findings shown before a run; blockers must be fixed. */
export function PrecheckPanel({ result, loading, error, acknowledged, onAcknowledge, onRerun, onApplyDateFormat }: Props) {
  return <section className="precheck-panel" aria-label="Data quality check">
    <div className="section-heading">
      <div><h2>Data quality check</h2><p>Your data is checked exactly as the run will read it: blank and repeated keys, keys missing from the other file, numbers stored as text, and date formats.</p></div>
      <button type="button" className="secondary" onClick={onRerun} disabled={loading}><RefreshCw size={15} />Check again</button>
    </div>
    {loading && <div className="loading-state"><Loader2 className="animate-spin" /> Checking your data…</div>}
    {error && <p className="error-text"><AlertTriangle size={16} />{error}</p>}
    {!loading && result && <>
      {result.status === "ok" && <p className="success-text"><CheckCircle2 size={16} />No problems found. You can run the reconciliation.</p>}
      {result.rules.map((rule) => {
        const grouped = issuesBySeverity(rule.issues);
        const visible = [...grouped.blocker, ...grouped.warning, ...grouped.info];
        if (!visible.length) return null;
        return <div className="precheck-rule" key={rule.sheet_rule_id}>
          <h3>{rule.label}{rule.source_rows !== undefined && <small>{rule.source_rows.toLocaleString()} source rows · {rule.destination_rows?.toLocaleString()} destination rows</small>}</h3>
          {(["blocker", "warning", "info"] as const).map((severity) => grouped[severity].length > 0 && <div key={severity}>
            <h4 className={`is-${severity}`}>{TITLES[severity]}</h4>
            <ul>{grouped[severity].map((issue, index) => <IssueRow key={`${severity}-${index}`} issue={issue} onFix={issue.suggestion?.date_format && onApplyDateFormat ? () => onApplyDateFormat(rule.sheet_rule_id, issue.suggestion!.date_format!) : undefined} />)}</ul>
          </div>)}
        </div>;
      })}
      {result.summary.blocker > 0 && <p className="error-text"><XCircle size={16} />Fix the items under "Must be fixed" (go back to the earlier steps), then check again.</p>}
      {result.summary.blocker === 0 && result.summary.warning > 0 && <label className="checkbox-line precheck-ack">
        <input type="checkbox" checked={acknowledged} onChange={(event) => onAcknowledge(event.target.checked)} />
        I have reviewed these warnings and want to run the reconciliation anyway (recorded in the audit log).
      </label>}
    </>}
  </section>;
}
