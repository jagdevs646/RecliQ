import { useEffect, useState } from "react";
import { Eye, FileSpreadsheet, ShieldCheck } from "lucide-react";
import { api } from "../services/api";
import type { AdminUser } from "../types";

type PreviewScope = "guest" | "registered" | "account";
type PreviewPolicy = { features: Record<string, boolean>; limits: Record<string, number | string[]> };
type AdminPolicy = { features: Record<string, boolean>; limits: Record<string, Record<string, number | string[]>>; available_formats: Array<{ extension: string; label: string; content_type: string }> };

const steps = [
  ["Upload", "Choose the reconciliation type and add workbooks."],
  ["Pair sheets", "Select the source and destination sheets."],
  ["Matching key", "Choose the fields that identify corresponding records."],
  ["Map columns", "Compare selected fields and configure amount/date rules."],
  ["Report setup", "Review output naming and optional delivery controls."],
  ["Run", "Start the job and view its status and results."],
];
const featureLabels: Record<string, string> = {
  basic_reconciliation: "General reconciliation", gst_reconciliation: "GST reconciliation", near_match_review: "Near-match review",
  auto_resolution: "Auto-resolution", saved_setups: "Saved setups", report_email: "Report email",
};
const screens: Array<{ label: string; feature?: string }> = [
  { label: "Dashboard" }, { label: "Reconcile" }, { label: "History" }, { label: "Saved setups", feature: "saved_setups" },
  { label: "Name aliases", feature: "near_match_review" }, { label: "Auto-resolution", feature: "auto_resolution" },
  { label: "Learning", feature: "near_match_review" }, { label: "Settings" },
];

export function AdminExperiencePreview({ policyOverride, isDraft = false }: { policyOverride?: { features: Record<string, boolean>; limits: Record<string, Record<string, number | string[]>> }; isDraft?: boolean }) {
  const [policy, setPolicy] = useState<AdminPolicy | null>(null);
  const [scope, setScope] = useState<PreviewScope>("guest");
  const [stage, setStage] = useState(0);
  const [userQuery, setUserQuery] = useState("");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [selectedUser, setSelectedUser] = useState<PreviewPolicy | null>(null);
  const [selectedUserLabel, setSelectedUserLabel] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (policyOverride) { setLoading(false); setError(""); return; }
    let current = true;
    void api.adminPolicy().then((result) => { if (current) setPolicy(result); }).catch((caught) => { if (current) setError(caught instanceof Error ? caught.message : "Policy preview could not be loaded."); }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [policyOverride]);

  useEffect(() => {
    if (scope !== "account" || !userQuery.trim()) { setUsers([]); return; }
    let current = true;
    const timer = window.setTimeout(() => {
      void api.adminUsers({ q: userQuery.trim(), page: 1, page_size: 25 }).then((result) => { if (current) setUsers(result.users); }).catch((caught) => { if (current) setError(caught instanceof Error ? caught.message : "Account search failed."); });
    }, 300);
    return () => { current = false; window.clearTimeout(timer); };
  }, [scope, userQuery]);

  async function selectAccount(user: AdminUser) {
    setError(""); setSelectedUser(null);
    try {
      const result = await api.adminUserDetail(user.id) as { features: Record<string, boolean>; limits: Record<string, number | string[]> };
      setSelectedUser({ features: result.features, limits: result.limits });
      setSelectedUserLabel(`${user.display_name} · ${user.username} · ${user.id}`);
      setUsers([]); setUserQuery(user.username);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Effective account policy could not be loaded."); }
  }

  const previewSource = policyOverride ?? policy;
  const currentPolicy: PreviewPolicy | null = scope === "account" ? selectedUser : previewSource ? {
    features: previewSource.features,
    limits: previewSource.limits[scope === "guest" ? "guest" : "registered"] ?? {},
  } : null;
  const formats = (currentPolicy?.limits.allowed_formats as string[] | undefined) ?? [];
  const visibleScreens = currentPolicy ? screens.filter((screen) => !screen.feature || currentPolicy.features[screen.feature] !== false) : [];

  return <section className="admin-section experience-preview">
    <div className="section-heading"><div><span className="eyebrow">Read-only policy preview</span><h2>User experience preview</h2><p>Inspect the controls and navigation available under a workspace policy without impersonating a customer.</p></div><Eye size={20} aria-hidden="true" /></div>
    {isDraft && <p className="preview-draft-banner" role="status">Previewing unsaved policy changes. This does not publish them or affect any workspace.</p>}
    <div className="preview-controls"><label>Workspace type<select value={scope} onChange={(event) => { setScope(event.target.value as PreviewScope); setSelectedUser(null); setSelectedUserLabel(""); setUsers([]); setUserQuery(""); }}><option value="guest">Guest user</option><option value="registered">Registered user default</option><option value="account">Specific account</option></select></label>
      {scope === "account" && <div className="preview-account-picker"><label htmlFor="preview-account-search">Find an account<input id="preview-account-search" value={userQuery} onChange={(event) => { setUserQuery(event.target.value); setSelectedUser(null); setSelectedUserLabel(""); }} placeholder="Username, name, email, or ID" /></label>{users.length > 0 && <div className="preview-account-results" role="list" aria-label="Matching accounts">{users.map((user) => <button type="button" role="listitem" key={user.id} onClick={() => void selectAccount(user)}><strong>{user.display_name}</strong><small>{user.username} · {user.email}</small></button>)}</div>}{selectedUserLabel && <p className="preview-selected-account">Effective policy: {selectedUserLabel}</p>}</div>}
    </div>
    {error && <p className="error-text" role="alert">{error}</p>}
    {loading ? <p className="admin-insights-state" role="status">Loading current policy…</p> : currentPolicy ? <>
      <div className="experience-stage-layout"><nav className="experience-stage-list" aria-label="Reconciliation preview stages">{steps.map(([label], index) => <button type="button" key={label} className={stage === index ? "is-active" : ""} aria-current={stage === index ? "step" : undefined} onClick={() => setStage(index)}><span>{index + 1}</span>{label}</button>)}</nav>
        <div className="experience-stage-detail"><div className="section-heading"><div><span className="eyebrow">Stage {stage + 1} of {steps.length}</span><h3>{steps[stage][0]}</h3><p>{steps[stage][1]}</p></div><FileSpreadsheet size={19} aria-hidden="true" /></div>
          {stage === 0 && <div className="preview-policy-lines">{[["basic_reconciliation", "General reconciliation"], ["gst_reconciliation", "GST reconciliation"]].map(([key, label]) => <PolicyLine key={key} label={label} enabled={Boolean(currentPolicy.features[key])} />)}<div className="preview-limit-line"><span>Allowed upload formats</span><strong>{formats.length ? formats.join(", ") : "No formats enabled"}</strong></div><div className="preview-limit-line"><span>Maximum file size</span><strong>{currentPolicy.limits.max_file_size_mb ?? "Not configured"} MB</strong></div></div>}
          {stage === 1 && <div className="preview-policy-lines"><p>Sheet pairing is part of the current reconciliation workflow; no independent sheet-pairing policy switch is configured.</p><div className="preview-limit-line"><span>Files per run</span><strong>{currentPolicy.limits.max_files_per_run ?? "Not configured"}</strong></div></div>}
          {stage === 2 && <div className="preview-policy-lines"><p>Matching-key selection is available when at least one reconciliation type is enabled.</p><PolicyLine label="General reconciliation" enabled={Boolean(currentPolicy.features.basic_reconciliation)} /><PolicyLine label="GST reconciliation" enabled={Boolean(currentPolicy.features.gst_reconciliation)} /></div>}
          {stage === 3 && <div className="preview-policy-lines"><p>Column mapping and multi-column rules remain part of the active workflow. Near-match review follows the workspace feature policy.</p><PolicyLine label="Near-match review" enabled={Boolean(currentPolicy.features.near_match_review)} /><PolicyLine label="Rows per sheet" enabled={Number(currentPolicy.limits.max_rows_per_sheet) > 0} value={String(currentPolicy.limits.max_rows_per_sheet ?? "Not configured")} /></div>}
          {stage === 4 && <div className="preview-policy-lines"><p>Report generation follows the existing reconciliation flow. Email is shown only when the feature is enabled.</p><PolicyLine label="Report email" enabled={Boolean(currentPolicy.features.report_email)} /><div className="preview-limit-line"><span>Recipients per report</span><strong>{currentPolicy.limits.max_email_recipients ?? "Not configured"}</strong></div></div>}
          {stage === 5 && <div className="preview-policy-lines"><p>Reconciliation can be started only when its type is enabled and workspace quotas permit it.</p><div className="preview-limit-line"><span>Runs per day</span><strong>{currentPolicy.limits.jobs_per_day ?? "Not configured"}</strong></div><div className="preview-limit-line"><span>Runs per month</span><strong>{currentPolicy.limits.jobs_per_month ?? "Not configured"}</strong></div><div className="preview-limit-line"><span>Concurrent runs</span><strong>{currentPolicy.limits.concurrent_jobs ?? "Not configured"}</strong></div></div>}
        </div></div>
      <section className="preview-navigation"><h3>Workspace navigation</h3><div>{visibleScreens.map((screen) => <span key={screen.label}>{screen.label}</span>)}</div></section>
      <p className="preview-safety-note"><ShieldCheck size={16} aria-hidden="true" /> This view reads policy only. It cannot upload a file, create a reconciliation, consume quota, view customer files, or send email.</p>
    </> : scope === "account" ? <div className="admin-insights-state">Search and select an account to inspect its effective policy.</div> : null}
  </section>;
}

function PolicyLine({ label, enabled, value }: { label: string; enabled: boolean; value?: string }) {
  return <div className="preview-limit-line"><span>{label}</span><strong className={enabled ? "is-enabled" : "is-disabled"}>{value ?? (enabled ? "Available" : "Unavailable")}</strong></div>;
}
