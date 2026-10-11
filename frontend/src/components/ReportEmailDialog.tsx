import { Loader2, Mail, RefreshCw, ShieldCheck, X } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { AutoResizeTextarea } from "./AutoResizeTextarea";
import { api } from "../services/api";
import type { Job, ReportEmailDelivery } from "../types";

interface Props {
  job: Job;
  onClose: () => void;
}

export function ReportEmailDialog({ job, onClose }: Props) {
  const [recipientsText, setRecipientsText] = useState("");
  const [subject, setSubject] = useState(`RecliQ report: ${job.display_name || job.input_file_1_name || "Reconciliation"}`);
  const [message, setMessage] = useState("");
  const [deliveryMethod, setDeliveryMethod] = useState<"attachment" | "secure_link">("secure_link");
  const [includeSummary, setIncludeSummary] = useState(true);
  const [deliveries, setDeliveries] = useState<ReportEmailDelivery[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function refresh() {
    try { setDeliveries(await api.listReportEmails(job.id)); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not load report delivery status."); }
  }

  useEffect(() => { void refresh(); }, [job.id]);
  useEffect(() => {
    if (!deliveries.some((delivery) => delivery.status === "queued" || delivery.status === "processing")) return;
    const timer = window.setInterval(() => { void refresh(); }, 2000);
    return () => window.clearInterval(timer);
  }, [job.id, deliveries]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const recipients = recipientsText.split(/[\s,;]+/).map((value) => value.trim()).filter(Boolean);
    if (!recipients.length) { setError("Enter at least one recipient email address."); return; }
    if (recipients.some((value) => !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value))) { setError("Check each recipient email address."); return; }
    if (!subject.trim()) { setError("Enter a subject for the report email."); return; }
    setBusy(true); setError(""); setNotice("");
    try {
      await api.sendReportEmail(job.id, {
        recipients,
        subject: subject.trim(),
        message: message.trim(),
        include_summary: includeSummary,
        delivery_method: deliveryMethod,
        idempotency_key: typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      });
      setRecipientsText("");
      await refresh();
      setNotice("The report email request was queued. Delivery status will update below.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not queue the report email."); }
    finally { setBusy(false); }
  }

  async function retry(delivery: ReportEmailDelivery) {
    setBusy(true); setError(""); setNotice("");
    try { await api.retryReportEmail(delivery.id); await refresh(); setNotice("The failed delivery was queued for retry."); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not retry delivery."); }
    finally { setBusy(false); }
  }

  async function revoke(delivery: ReportEmailDelivery) {
    setBusy(true); setError(""); setNotice("");
    try { await api.revokeReportLink(delivery.id); await refresh(); setNotice("The secure report link was revoked."); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not revoke the link."); }
    finally { setBusy(false); }
  }

  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="report-email-dialog" role="dialog" aria-modal="true" aria-labelledby="report-email-title">
      <div className="dialog-heading"><div><span className="eyebrow">Report delivery</span><h2 id="report-email-title">Email reconciliation report</h2><p>{job.display_name || `${job.input_file_1_name || "File 1"} vs ${job.input_file_2_name || "File 2"}`}</p></div><button className="icon-button" type="button" title="Close" aria-label="Close email dialog" onClick={onClose}><X size={17} /></button></div>
      <form className="auth-form" noValidate onSubmit={submit}>
        <label>Recipients<input value={recipientsText} onChange={(event) => setRecipientsText(event.target.value)} placeholder="name@example.com, finance@example.com" required /><small>Separate multiple addresses with commas, spaces, or semicolons.</small></label>
        <label>Subject<input maxLength={300} value={subject} onChange={(event) => setSubject(event.target.value)} required /></label>
        <label>Message<AutoResizeTextarea rows={3} maxLength={3000} value={message} onChange={(event) => setMessage(event.target.value)} /></label>
        <div className="email-delivery-options" role="group" aria-label="Delivery method">
          <label className={`email-method ${deliveryMethod === "secure_link" ? "is-active" : ""}`}><input type="radio" name="delivery-method" checked={deliveryMethod === "secure_link"} onChange={() => setDeliveryMethod("secure_link")} /><ShieldCheck size={17} /><span><strong>Secure link</strong><small>Recipient-specific and expires automatically</small></span></label>
          <label className={`email-method ${deliveryMethod === "attachment" ? "is-active" : ""}`}><input type="radio" name="delivery-method" checked={deliveryMethod === "attachment"} onChange={() => setDeliveryMethod("attachment")} /><Mail size={17} /><span><strong>Attachment</strong><small>Include the generated report file</small></span></label>
        </div>
        <label className="check-row"><input type="checkbox" checked={includeSummary} onChange={(event) => setIncludeSummary(event.target.checked)} /><span>Include reconciliation summary</span></label>
        {error && <p className="error-text" role="alert">{error}</p>}{notice && <p className="success-text" role="status">{notice}</p>}
        <button type="submit" className="primary" disabled={busy}>{busy ? <Loader2 size={16} className="animate-spin" /> : <Mail size={16} />}Queue report email</button>
      </form>
      <section className="delivery-history">
        <div className="section-heading"><div><h3>Recent deliveries</h3><p>Delivery state is refreshed automatically while queued.</p></div><button type="button" className="icon-button" title="Refresh delivery status" aria-label="Refresh delivery status" onClick={() => void refresh()}><RefreshCw size={15} /></button></div>
        {deliveries.length ? <div className="delivery-list">{deliveries.map((delivery) => <article className="delivery-row" key={delivery.id}>
          <div className="delivery-detail"><strong>{delivery.recipients.join(", ")}</strong><small>{delivery.subject} · {new Date(delivery.created_at).toLocaleString()}</small><small className={`delivery-status is-${delivery.status}`}>{delivery.status}{delivery.transport === "local_log" ? " · logged locally, not emailed" : ""}{delivery.error_message ? ` · ${delivery.error_message}` : ""}</small></div>
          <div className="delivery-actions">{delivery.status === "failed" && <button type="button" className="secondary" disabled={busy} onClick={() => void retry(delivery)}>Retry</button>}{delivery.delivery_method === "secure_link" && delivery.status === "sent" && <button type="button" className="text-command is-danger" disabled={busy} onClick={() => void revoke(delivery)}>Revoke link</button>}</div>
        </article>)}</div> : <p className="muted">No delivery attempts for this report.</p>}
      </section>
    </section>
  </div>;
}
