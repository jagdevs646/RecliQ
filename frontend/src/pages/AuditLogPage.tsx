import { Download, Loader2, ShieldAlert, ShieldCheck } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import { api } from "../services/api";
import { AUDIT_ACTIONS, auditActionLabel } from "../lib/audit";
import { formatServerTime } from "../lib/formats";
import type { AuditEvent } from "../types";

const PAGE_SIZE = 50;

/** Append-only audit log: who did what, when, with before/after values. */
export function AuditLogPage() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [page, setPage] = useState(0);
  const [action, setAction] = useState("");
  const [loading, setLoading] = useState(true);
  const [verification, setVerification] = useState<{ valid: boolean; checked: number; reason: string } | null>(null);
  const [message, setMessage] = useState("");
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    setLoading(true);
    api.listAuditEvents({ action: action || undefined, offset: page * PAGE_SIZE, limit: PAGE_SIZE })
      .then(setEvents)
      .catch((error: Error) => setMessage(error.message))
      .finally(() => setLoading(false));
  }, [page, action]);

  useEffect(() => { api.verifyAuditLog().then(setVerification).catch(() => undefined); }, []);

  async function download(format: "csv" | "json") {
    try {
      await api.downloadAuditLog(format);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Export failed");
    }
  }

  return <section className="page">
    <div className="page-title"><div><span className="eyebrow">Controls</span><h1>Audit log</h1><p>Every change and decision, with before and after values. Entries cannot be edited or deleted.</p></div>
      <div className="page-actions"><button type="button" className="secondary" onClick={() => download("csv")}><Download size={15} />Export CSV</button><button type="button" className="secondary" onClick={() => download("json")}><Download size={15} />Export JSON</button></div></div>
    {verification && <p className={verification.valid ? "success-text" : "error-text"}>{verification.valid ? <ShieldCheck size={16} /> : <ShieldAlert size={16} />}
      {verification.valid ? `Integrity verified: all ${verification.checked} entries are unchanged since they were recorded.` : `Integrity check failed: ${verification.reason}`}</p>}
    {message && <p className="error-text">{message}</p>}
    <div className="audit-toolbar">
      <label><span>Action</span><select value={action} onChange={(event) => { setAction(event.target.value); setPage(0); }}>
        <option value="">All actions</option>
        {AUDIT_ACTIONS.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
      </select></label>
      <p className="muted">Until sign-in is enabled, "Actor" is the browser session or the system component.</p>
    </div>
    <div className="table-scroll"><table><thead><tr><th>#</th><th>When (UTC)</th><th>Action</th><th>What happened</th><th>Actor</th><th>Record</th></tr></thead><tbody>
      {loading ? <tr><td colSpan={6}><Loader2 size={16} className="animate-spin" /> Loading…</td></tr> : events.length === 0 ? <tr><td colSpan={6}>No entries.</td></tr> : events.map((event) => <Fragment key={event.event_id}>
        <tr onClick={() => setOpen(open === event.sequence ? null : event.sequence)} className="clickable-row">
          {/* The number opens the entry's details from the keyboard as well. */}
          <td><button type="button" className="row-link" aria-expanded={open === event.sequence} title="Show before and after values" onClick={(clicked) => { clicked.stopPropagation(); setOpen(open === event.sequence ? null : event.sequence); }}>{event.sequence}</button></td><td title={event.occurred_at}>{formatServerTime(event.occurred_at)}</td><td title={event.action}>{auditActionLabel(event.action)}</td><td>{event.summary}</td>
          <td>{event.actor_type === "system" ? `System (${event.actor_id})` : `Session ${event.actor_id.slice(0, 8)}…`}</td><td>{event.entity_type}{event.entity_id ? ` ${event.entity_id.slice(0, 8)}…` : ""}</td>
        </tr>
        {open === event.sequence && <tr className="detail-row"><td colSpan={6}><pre>{JSON.stringify({ before: event.before, after: event.after, metadata: event.metadata, ip: event.ip_address, request_id: event.request_id }, null, 2)}</pre></td></tr>}
      </Fragment>)}
    </tbody></table></div>
    <div className="pagination"><span>Page {page + 1}</span><div><button type="button" className="secondary" onClick={() => setPage((current) => Math.max(0, current - 1))} disabled={page === 0}>Newer</button><button type="button" className="secondary" onClick={() => setPage((current) => current + 1)} disabled={events.length < PAGE_SIZE}>Older</button></div></div>
  </section>;
}
