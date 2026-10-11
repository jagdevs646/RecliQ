import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowUpRight, CircleHelp, LifeBuoy, MessageSquareText, Send, ShieldCheck } from "lucide-react";
import { api } from "../services/api";
import { AutoResizeTextarea } from "../components/AutoResizeTextarea";
import type { SupportTicket, SupportTicketMessage } from "../types";

type Draft = {
  category: string;
  custom_area: string;
  workflow_stage: string;
  impact: string;
  subject: string;
  description: string;
  expected_behavior: string;
  reproduction_steps: string;
};

const emptyDraft: Draft = {
  category: "reconciliation",
  custom_area: "",
  workflow_stage: "other",
  impact: "unable_to_complete",
  subject: "",
  description: "",
  expected_behavior: "",
  reproduction_steps: "",
};

const categories = [
  ["account_access", "Account access"], ["file_upload", "File upload"], ["reconciliation", "Reconciliation"],
  ["matching_and_mapping", "Matching and column mapping"], ["results_and_reports", "Results and reports"],
  ["saved_setups", "Saved setups"], ["aliases", "Name aliases"], ["auto_resolution", "Auto-resolution"],
  ["learning", "Learning"], ["performance", "Performance"], ["privacy_security", "Privacy or security"], ["other", "Other"],
];

const stages = [
  ["dashboard", "Dashboard"], ["upload", "Upload files"], ["sheet_pairing", "Pair sheets"],
  ["matching_key", "Choose matching keys"], ["column_mapping", "Map columns"], ["report_setup", "Report setup"],
  ["running", "Run reconciliation"], ["results", "Results"], ["history", "History"],
  ["account_access", "Account access"], ["settings", "Settings"], ["other", "Other / not sure"],
];

const impacts = [
  ["blocked", "I am blocked"], ["incorrect_results", "Results may be incorrect"],
  ["unable_to_complete", "I cannot complete this step"], ["question", "I have a question"], ["visual_issue", "Display or usability issue"],
];

export function SupportPage() {
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [tickets, setTickets] = useState<SupportTicket[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<SupportTicket | null>(null);
  const [reply, setReply] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [replyBusy, setReplyBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [listError, setListError] = useState("");
  const subjectRef = useRef<HTMLInputElement>(null);
  const descriptionRef = useRef<HTMLTextAreaElement>(null);
  const customAreaRef = useRef<HTMLInputElement>(null);

  async function loadTickets() {
    setListError("");
    try {
      const result = await api.listSupportTickets();
      setTickets(result.tickets);
      setTotal(result.total);
      if (!selectedId && result.tickets[0]) setSelectedId(result.tickets[0].id);
    } catch (caught) {
      setListError(caught instanceof Error ? caught.message : "Support requests could not be loaded.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void loadTickets(); }, []);

  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    let current = true;
    setDetail(null);
    void api.supportTicket(selectedId).then((ticket) => { if (current) setDetail(ticket); }).catch((caught) => {
      if (current) setError(caught instanceof Error ? caught.message : "This support request is no longer available.");
    });
    return () => { current = false; };
  }, [selectedId]);

  function setField<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({ ...current, [key]: "" }));
  }

  function validate(): Record<string, string> {
    const next: Record<string, string> = {};
    if (draft.subject.trim().length < 5) next.subject = "Enter a short title (at least 5 characters).";
    if (draft.description.trim().length < 10) next.description = "Describe what happened (at least 10 characters).";
    if (draft.category === "other" && !draft.custom_area.trim()) next.custom_area = "Tell us which area this relates to.";
    return next;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const next = validate();
    setErrors(next);
    const first = Object.keys(next)[0];
    if (first) {
      ({ subject: subjectRef, description: descriptionRef, custom_area: customAreaRef }[first as "subject" | "description" | "custom_area"]?.current)?.focus();
      return;
    }
    setSaving(true); setError(""); setMessage("");
    try {
      const created = await api.createSupportTicket({ ...draft, subject: draft.subject.trim(), description: draft.description.trim(), custom_area: draft.category === "other" ? draft.custom_area.trim() : "", expected_behavior: draft.expected_behavior.trim(), reproduction_steps: draft.reproduction_steps.trim() });
      setDraft(emptyDraft); setSelectedId(created.id); setMessage("Support request sent. RecliQ will reply here."); await loadTickets();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The support request could not be sent. Your text is still here; try again.");
    } finally { setSaving(false); }
  }

  async function sendReply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail || !reply.trim() || replyBusy) return;
    setReplyBusy(true); setError(""); setMessage("");
    try {
      await api.replyToSupportTicket(detail.id, reply.trim());
      setReply("");
      setDetail(await api.supportTicket(detail.id));
      await loadTickets();
      setMessage("Reply sent.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Your reply could not be sent. It is still in the box; try again.");
    } finally { setReplyBusy(false); }
  }

  return <section className="page support-page">
    <div className="page-title"><div><span className="eyebrow">Help and follow-up</span><h1>Support</h1><p>Tell us where you got stuck. Replies stay in this workspace.</p></div><LifeBuoy size={22} aria-hidden="true" /></div>
    {message && <p className="success-text" role="status">{message}</p>}
    {error && <p className="error-text" role="alert">{error}</p>}
    <div className="support-layout">
      <section className="admin-section support-form-panel" aria-labelledby="support-form-title">
        <div className="section-heading"><div><h2 id="support-form-title">Raise a query or complaint</h2><p>Share enough detail for us to find the affected step.</p></div><CircleHelp size={19} /></div>
        <form className="support-form" noValidate onSubmit={(event) => void submit(event)}>
          <div className="support-form-row">
            <label htmlFor="support-category">Area<select id="support-category" value={draft.category} onChange={(event) => setField("category", event.target.value)}>{categories.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label htmlFor="support-stage">Workflow step<select id="support-stage" value={draft.workflow_stage} onChange={(event) => setField("workflow_stage", event.target.value)}>{stages.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          </div>
          {draft.category === "other" && <label htmlFor="support-custom-area">Which area?<input ref={customAreaRef} id="support-custom-area" maxLength={120} value={draft.custom_area} aria-invalid={Boolean(errors.custom_area)} aria-describedby={errors.custom_area ? "support-custom-area-error" : undefined} onChange={(event) => setField("custom_area", event.target.value)} />{errors.custom_area && <small className="field-error" id="support-custom-area-error">{errors.custom_area}</small>}</label>}
          <label htmlFor="support-impact">Impact<select id="support-impact" value={draft.impact} onChange={(event) => setField("impact", event.target.value)}>{impacts.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label htmlFor="support-subject">Title<input ref={subjectRef} id="support-subject" maxLength={160} value={draft.subject} aria-invalid={Boolean(errors.subject)} aria-describedby={errors.subject ? "support-subject-error" : undefined} onChange={(event) => setField("subject", event.target.value)} />{errors.subject && <small className="field-error" id="support-subject-error">{errors.subject}</small>}</label>
          <label htmlFor="support-description">What happened?<AutoResizeTextarea ref={descriptionRef} id="support-description" rows={5} maxLength={6000} value={draft.description} aria-invalid={Boolean(errors.description)} aria-describedby={`support-description-help${errors.description ? " support-description-error" : ""}`} onChange={(event) => setField("description", event.target.value)} /><small id="support-description-help">Include the message shown and what you were doing. {draft.description.length}/6000</small>{errors.description && <small className="field-error" id="support-description-error">{errors.description}</small>}</label>
          <details className="support-more-details"><summary>More details (optional)</summary><div className="support-optional-fields"><label htmlFor="support-expected">What did you expect?<AutoResizeTextarea id="support-expected" rows={3} maxLength={3000} value={draft.expected_behavior} onChange={(event) => setField("expected_behavior", event.target.value)} /></label><label htmlFor="support-steps">Steps to reproduce<AutoResizeTextarea id="support-steps" rows={4} maxLength={5000} value={draft.reproduction_steps} onChange={(event) => setField("reproduction_steps", event.target.value)} /></label></div></details>
          <p className="support-privacy-note"><ShieldCheck size={16} aria-hidden="true" /> Do not include workbook contents, passwords, or other confidential data. File attachments are not accepted.</p>
          <button className="primary" type="submit" disabled={saving} aria-busy={saving}><Send size={16} aria-hidden="true" />{saving ? "Sending…" : "Send to RecliQ"}</button>
        </form>
      </section>

      <section className="admin-section support-history-panel" aria-labelledby="support-history-title">
        <div className="section-heading"><div><h2 id="support-history-title">Your requests</h2><p>{total} request{total === 1 ? "" : "s"} in this workspace</p></div><button className="icon-button" type="button" aria-label="Refresh support requests" title="Refresh" onClick={() => void loadTickets()}>↻</button></div>
        {listError && <div className="support-inline-error" role="alert"><span>{listError}</span><button className="secondary compact" type="button" onClick={() => void loadTickets()}>Retry</button></div>}
        {loading ? <div className="support-list-state" role="status">Loading support requests…</div> : tickets.length ? <div className="support-ticket-list">{tickets.map((ticket) => <button className={`support-ticket-row ${selectedId === ticket.id ? "is-selected" : ""}`} type="button" key={ticket.id} aria-current={selectedId === ticket.id ? "true" : undefined} onClick={() => { setSelectedId(ticket.id); setMessage(""); setError(""); }}><span><strong>{ticket.subject}</strong><small>{labelFor(categories, ticket.category)} · {formatDate(ticket.updated_at)}</small></span><span className={`support-status is-${ticket.status}`}>{statusLabel(ticket.status)}</span><ArrowUpRight size={15} aria-hidden="true" /></button>)}</div> : <div className="support-list-state"><MessageSquareText size={20} aria-hidden="true" /><strong>No support requests yet</strong><span>When you send a query, its status and replies will appear here.</span></div>}
        {detail && <TicketConversation ticket={detail} reply={reply} onReplyChange={setReply} onReply={sendReply} busy={replyBusy} />}
      </section>
    </div>
  </section>;
}

function TicketConversation({ ticket, reply, onReplyChange, onReply, busy }: { ticket: SupportTicket; reply: string; onReplyChange: (value: string) => void; onReply: (event: FormEvent<HTMLFormElement>) => void; busy: boolean }) {
  const messages: SupportTicketMessage[] = ticket.messages ?? [];
  return <div className="support-conversation">
    <div className="support-ticket-heading"><div><span className="eyebrow">Request detail</span><h3>{ticket.subject}</h3></div><span className={`support-status is-${ticket.status}`}>{statusLabel(ticket.status)}</span></div>
    <dl className="support-ticket-facts"><div><dt>Area</dt><dd>{ticket.category === "other" ? ticket.custom_area : labelFor(categories, ticket.category)}</dd></div><div><dt>Step</dt><dd>{labelFor(stages, ticket.workflow_stage)}</dd></div><div><dt>Impact</dt><dd>{labelFor(impacts, ticket.impact)}</dd></div><div><dt>Submitted</dt><dd>{formatDate(ticket.created_at)}</dd></div></dl>
    <article className="support-message is-user"><strong>Your report</strong><p>{ticket.description}</p>{ticket.expected_behavior && <><strong>Expected behavior</strong><p>{ticket.expected_behavior}</p></>}{ticket.reproduction_steps && <><strong>Steps to reproduce</strong><p>{ticket.reproduction_steps}</p></>}</article>
    {messages.map((item) => <article className={`support-message is-${item.author_type}`} key={item.id}><div><strong>{item.author_type === "admin" ? "RecliQ support" : "You"}</strong><time dateTime={item.created_at}>{formatDate(item.created_at)}</time></div><p>{item.body}</p></article>)}
    <form className="support-reply-form" noValidate onSubmit={onReply}><label htmlFor="support-reply">Reply to this request<AutoResizeTextarea id="support-reply" rows={3} maxLength={6000} value={reply} onChange={(event) => onReplyChange(event.target.value)} /></label><button type="submit" className="secondary" disabled={busy || !reply.trim()} aria-busy={busy}><Send size={15} aria-hidden="true" />{busy ? "Sending…" : "Send reply"}</button></form>
  </div>;
}

function labelFor(options: string[][], key: string) { return options.find(([value]) => value === key)?.[1] ?? key.replace(/_/g, " "); }
function statusLabel(value: string) { return value.replace(/_/g, " "); }
function formatDate(value: string) { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
