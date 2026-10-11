import { useEffect, useRef, useState, type FormEvent } from "react";
import { Filter, MessageSquareText, Search, Send, X } from "lucide-react";
import { AppModal } from "../components/AppModal";
import { AutoResizeTextarea } from "../components/AutoResizeTextarea";
import { api } from "../services/api";
import type { SupportTicket } from "../types";

const categories = [
  ["account_access", "Account access"], ["file_upload", "File upload"], ["reconciliation", "Reconciliation"],
  ["matching_and_mapping", "Matching and mapping"], ["results_and_reports", "Results and reports"],
  ["saved_setups", "Saved setups"], ["aliases", "Name aliases"], ["auto_resolution", "Auto-resolution"],
  ["learning", "Learning"], ["performance", "Performance"], ["privacy_security", "Privacy or security"], ["other", "Other"],
];
const pageSizes = [25, 50, 100];

export function AdminComplaintsPanel({ initialUserId = "" }: { initialUserId?: string }) {
  const [tickets, setTickets] = useState<SupportTicket[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<SupportTicket | null>(null);
  const draftsForTicket = useRef("");
  const [summary, setSummary] = useState<{ total: number; unresolved: number; unread: number; new: number; urgent: number; high: number; by_category: Record<string, number> } | null>(null);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState("");
  const [priority, setPriority] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [detailAttempt, setDetailAttempt] = useState(0);
  const [statusDraft, setStatusDraft] = useState<SupportTicket["status"]>("new");
  const [priorityDraft, setPriorityDraft] = useState<SupportTicket["priority"]>("normal");
  const [reply, setReply] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    void api.markAdminComplaintsRead().catch(() => undefined);
    void api.adminComplaintSummary().then(setSummary).catch(() => undefined);
  }, []);

  useEffect(() => {
    let current = true;
    const timer = window.setTimeout(() => {
      setLoading(true); setError("");
      void api.adminComplaints({ q: query.trim() || undefined, category: category || undefined, status: status || undefined, priority: priority || undefined, user_id: initialUserId || undefined, page, page_size: pageSize }).then((result) => {
        if (!current) return;
        setTickets(result.tickets); setTotal(result.total);
      }).catch((caught) => { if (current) setError(caught instanceof Error ? caught.message : "Complaints could not be loaded."); })
        .finally(() => { if (current) setLoading(false); });
    }, 300);
    return () => { current = false; window.clearTimeout(timer); };
  }, [query, category, status, priority, initialUserId, page, pageSize]);

  useEffect(() => {
    if (!selectedId) { setDetail(null); setDetailLoading(false); setDetailError(""); return; }
    let current = true;
    setDetail(null);
    setDetailError("");
    setDetailLoading(true);
    void api.adminComplaint(selectedId).then((ticket) => {
      if (!current) return;
      setDetail(ticket);
      if (draftsForTicket.current !== ticket.id) {
        setStatusDraft(ticket.status); setPriorityDraft(ticket.priority);
        draftsForTicket.current = ticket.id;
      }
      void api.adminComplaintSummary().then(setSummary).catch(() => undefined);
    }).catch((caught) => { if (current) setDetailError(caught instanceof Error ? caught.message : "Complaint detail could not be loaded."); })
      .finally(() => { if (current) setDetailLoading(false); });
    return () => { current = false; };
  }, [selectedId, detailAttempt]);

  function changeFilter(change: () => void) { setPage(1); setSelectedId(""); setDetailOpen(false); setDetail(null); draftsForTicket.current = ""; setReply(""); setDetailError(""); setMessage(""); setError(""); change(); }

  function closeDetail() {
    setDetailOpen(false);
    setDetailError("");
    setMessage("");
  }

  function clearDetail() {
    closeDetail();
    setSelectedId("");
    setDetail(null);
    setDetailLoading(false);
    draftsForTicket.current = "";
    setReply("");
  }

  function openDetail(id: string) {
    if (selectedId !== id) {
      setSelectedId(id);
      setDetail(null);
      setDetailLoading(true);
      setReply("");
      draftsForTicket.current = "";
    }
    setDetailOpen(true);
    setDetailError("");
    setMessage("");
  }

  async function saveTriage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail || saving) return;
    setSaving(true); setDetailError(""); setMessage("");
    try {
      const updated = await api.updateAdminComplaint(detail.id, { status: statusDraft, priority: priorityDraft });
      setDetail(updated); setTickets((current) => current.map((ticket) => ticket.id === updated.id ? updated : ticket));
      setMessage("Complaint updated.");
      setSummary(await api.adminComplaintSummary());
    } catch (caught) { setDetailError(caught instanceof Error ? caught.message : "Complaint could not be updated. Try again."); }
    finally { setSaving(false); }
  }

  async function sendReply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail || !reply.trim() || sending) return;
    setSending(true); setDetailError(""); setMessage("");
    try {
      const updated = await api.updateAdminComplaint(detail.id, { reply: reply.trim() });
      setReply(""); setDetail(updated); setStatusDraft(updated.status);
      setTickets((current) => current.map((ticket) => ticket.id === updated.id ? updated : ticket));
      setSummary(await api.adminComplaintSummary());
      setMessage("Reply sent to the user's support workspace.");
    } catch (caught) { setDetailError(caught instanceof Error ? caught.message : "Reply could not be sent. Your text is still here; try again."); }
    finally { setSending(false); }
  }

  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  return <section className="admin-section complaint-section" aria-labelledby="admin-complaints-title">
    <div className="section-heading"><div><span className="eyebrow">User support queue</span><h2 id="admin-complaints-title">Complaints</h2><p>Read and respond to workspace-scoped requests. Source files and reconciliation documents are not accessible here.</p></div><MessageSquareText size={20} aria-hidden="true" /></div>
    {summary && <div className="complaint-summary-strip" aria-label="Complaint summary"><span><strong>{summary.unresolved.toLocaleString()}</strong> unresolved</span><span><strong>{summary.unread.toLocaleString()}</strong> unread</span><span className="is-urgent"><strong>{summary.urgent.toLocaleString()}</strong> urgent</span><span className="is-high"><strong>{summary.high.toLocaleString()}</strong> high priority</span></div>}
    {message && !detailOpen && <p className="success-text" role="status">{message}</p>}
    {error && !detailOpen && <p className="error-text" role="alert">{error}</p>}
    {initialUserId && <p className="support-user-filter"><span>Showing complaints for account <code>{initialUserId}</code></span><button className="secondary compact" type="button" onClick={() => window.location.hash = "#/admin/complaints"}>Show all complaints</button></p>}
    <div className="complaint-filters">
      <label className="search-field"><Search size={17} aria-hidden="true" /><span className="visually-hidden">Search complaints and requesters</span><input value={query} onChange={(event) => changeFilter(() => setQuery(event.target.value))} placeholder="Search title, message, username, email, or ID" />{query && <button className="icon-button" type="button" aria-label="Clear complaint search" title="Clear search" onClick={(event) => { changeFilter(() => setQuery("")); event.currentTarget.parentElement?.querySelector("input")?.focus(); }}><X size={15} /></button>}</label>
      <label><span><Filter size={14} aria-hidden="true" /> Category</span><select value={category} onChange={(event) => changeFilter(() => setCategory(event.target.value))}><option value="">All categories</option>{categories.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label><span>Status</span><select value={status} onChange={(event) => changeFilter(() => setStatus(event.target.value))}><option value="">All statuses</option><option value="new">New</option><option value="in_progress">In progress</option><option value="waiting_for_user">Waiting for user</option><option value="resolved">Resolved</option></select></label>
      <label><span>Priority</span><select value={priority} onChange={(event) => changeFilter(() => setPriority(event.target.value))}><option value="">All priorities</option><option value="urgent">Urgent</option><option value="high">High</option><option value="normal">Normal</option><option value="low">Low</option></select></label>
    </div>
    <div className="complaints-layout">
      <div className="complaint-list-panel">
        <div className="table-scroll-x"><table className="complaint-table"><thead><tr><th scope="col">Request</th><th scope="col">Requester</th><th scope="col">Priority</th><th scope="col">Status</th><th scope="col">Updated</th></tr></thead><tbody>
          {loading ? <tr><td colSpan={5} className="table-empty" role="status">Loading complaints…</td></tr> : tickets.length ? tickets.map((ticket) => <tr key={ticket.id} className={selectedId === ticket.id ? "is-selected" : ""}><td><button type="button" className="complaint-row-trigger" onClick={() => openDetail(ticket.id)}><strong>{ticket.subject}</strong><small>{categoryLabel(ticket.category, ticket.custom_area)} · {humanize(ticket.workflow_stage)}</small></button></td><td><strong>{ticket.requester.display_name}</strong><small>{ticket.requester.username || "Guest workspace"}{ticket.requester.email ? ` · ${ticket.requester.email}` : ""}</small></td><td><span className={`complaint-priority is-${ticket.priority}`}>{ticket.priority}</span></td><td><span className={`support-status is-${ticket.status}`}>{humanize(ticket.status)}</span></td><td>{dateLabel(ticket.updated_at)}</td></tr>) : <tr><td colSpan={5} className="table-empty">{error ? "Could not load matching complaints." : query || category || status || priority ? "No complaints match these filters." : "No complaints have been submitted."}</td></tr>}
        </tbody></table></div>
        <div className="pagination"><label>Rows<select value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1); clearDetail(); }} aria-label="Rows per page">{pageSizes.map((size) => <option key={size} value={size}>{size}</option>)}</select></label><span>{total ? `${((page - 1) * pageSize + 1).toLocaleString()}–${Math.min(page * pageSize, total).toLocaleString()} of ${total.toLocaleString()}` : "0 complaints"} · Page {page} of {pageCount}</span><div><button type="button" className="secondary compact" disabled={page <= 1} onClick={() => { setPage((value) => value - 1); clearDetail(); }}>Previous</button><button type="button" className="secondary compact" disabled={page >= pageCount} onClick={() => { setPage((value) => value + 1); clearDetail(); }}>Next</button></div></div>
      </div>
    </div>
    <AppModal open={detailOpen} title={detail?.subject ?? (detailLoading ? "Loading complaint details" : "Complaint details")} eyebrow={detail?.requester.display_name ?? "User support"} onClose={closeDetail}>
      {message && <p className="success-text" role="status">{message}</p>}
      {detailError && <p className="error-text" role="alert">{detailError}</p>}
      {detailLoading && <p className="admin-detail-load-state" role="status">Loading complaint details…</p>}
      {!detailLoading && !detail && detailError && <div className="admin-detail-load-state"><button type="button" className="secondary" onClick={() => { setDetailError(""); setDetailAttempt((attempt) => attempt + 1); }}>Retry</button></div>}
      {!detailLoading && !detail && !detailError && <p className="muted">Complaint details are unavailable.</p>}
      {detail && <section className="complaint-detail" aria-label="Selected complaint">
        <div className="complaint-detail-heading"><div><p>{detail.requester.username ? `${detail.requester.username} · ${detail.requester.email}` : `Guest · ${detail.requester.scope_id}`}</p></div><span className={`support-status is-${detail.status}`}>{humanize(detail.status)}</span></div>
        <div className="complaint-context-grid"><span><small>Category</small><strong>{categoryLabel(detail.category, detail.custom_area)}</strong></span><span><small>Step</small><strong>{humanize(detail.workflow_stage)}</strong></span><span><small>Impact</small><strong>{humanize(detail.impact)}</strong></span><span><small>Received</small><strong>{dateLabel(detail.created_at)}</strong></span></div>
        <article className="complaint-description"><strong>What happened</strong><p>{detail.description}</p>{detail.expected_behavior && <><strong>Expected behavior</strong><p>{detail.expected_behavior}</p></>}{detail.reproduction_steps && <><strong>Reproduction steps</strong><p>{detail.reproduction_steps}</p></>}</article>
        <div className="complaint-thread" aria-label="Conversation"><h4>Conversation</h4>{detail.messages?.length ? detail.messages.map((item) => <article className={`complaint-message is-${item.author_type}`} key={item.id}><div><strong>{item.author_type === "admin" ? "RecliQ support" : item.author_label}</strong><time dateTime={item.created_at}>{dateLabel(item.created_at)}</time></div><p>{item.body}</p></article>) : <p className="muted">No follow-up messages yet.</p>}</div>
        <form className="complaint-triage" noValidate onSubmit={(event) => void saveTriage(event)}><h4>Prioritize and update</h4><div className="complaint-triage-fields"><label>Status<select value={statusDraft} onChange={(event) => setStatusDraft(event.target.value as SupportTicket["status"])}><option value="new">New</option><option value="in_progress">In progress</option><option value="waiting_for_user">Waiting for user</option><option value="resolved">Resolved</option></select></label><label>Priority<select value={priorityDraft} onChange={(event) => setPriorityDraft(event.target.value as SupportTicket["priority"])}><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option><option value="urgent">Urgent</option></select></label></div><button className="secondary" type="submit" disabled={saving} aria-busy={saving}>{saving ? "Saving…" : "Save changes"}</button></form>
        <form className="complaint-reply" noValidate onSubmit={(event) => void sendReply(event)}><label htmlFor="admin-complaint-reply">Reply to requester<AutoResizeTextarea id="admin-complaint-reply" rows={4} maxLength={6000} value={reply} onChange={(event) => setReply(event.target.value)} /></label><small>The reply appears only in the requester's workspace.</small><button className="primary" type="submit" disabled={sending || !reply.trim()} aria-busy={sending}><Send size={15} aria-hidden="true" />{sending ? "Sending…" : "Send reply"}</button></form>
      </section>}
    </AppModal>
  </section>;
}

function categoryLabel(category: string, custom: string) { return category === "other" ? custom || "Other" : categories.find(([value]) => value === category)?.[1] ?? humanize(category); }
function humanize(value: string) { return value.replace(/_/g, " "); }
function dateLabel(value: string) { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
