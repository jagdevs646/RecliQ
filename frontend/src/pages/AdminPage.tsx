import { Activity, Database, FileText, HardDrive, Mail, MessageSquareText, RefreshCw, Search, Shield, Users, X } from "lucide-react";
import { useEffect, useRef, useState, type ChangeEvent, type FormEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { api } from "../services/api";
import { AppModal } from "../components/AppModal";
import { AutoResizeTextarea } from "../components/AutoResizeTextarea";
import type { AdminOverview, AdminSection, AdminUser } from "../types";
import { AdminComplaintsPanel } from "./AdminComplaintsPanel";
import { AdminProductInsights } from "./AdminProductInsights";
import { AdminExperiencePreview } from "./AdminExperiencePreview";

type AdminLimit = number | string[];
type AdminTab = AdminSection | "settings";
type AdminPolicy = { features: Record<string, boolean>; limits: Record<string, Record<string, AdminLimit>>; email_ready: boolean };
type PlatformEmailSettings = { sender_email: string; transport: "smtp" | "local_log" | "disabled"; ready: boolean; smtp_ready: boolean; smtp_host_configured: boolean; smtp_auth_configured: boolean; local_only: boolean };
type UserDetail = {
  user: { id: string; username: string; email: string; display_name: string; role: string; status: string; email_verified: boolean; mfa_enabled: boolean; created_at: string; last_login_at: string | null; last_seen_at: string | null };
  usage: AdminUser["usage"];
  limits: Record<string, AdminLimit>;
  features: Record<string, boolean>;
  feature_overrides: Record<string, boolean>;
  limit_overrides: Record<string, AdminLimit>;
  sessions: Array<{ id: string; last_seen_at: string; expires_at: string; ip_address: string | null; user_agent: string | null; revoked_at: string | null }>;
  report_emails: Array<{ id: string; job_id: string; status: string; recipients: string[]; created_at: string; sent_at: string | null; error_message: string | null }>;
  admin_events: Array<{ id: string; action: string; occurred_at: string; result: string }>;
  complaints: number;
};

const featureLabels: Record<string, string> = {
  basic_reconciliation: "Basic reconciliation", gst_reconciliation: "GST reconciliation", near_match_review: "Near-match review",
  auto_resolution: "Auto-resolution", saved_setups: "Saved setups", report_email: "Report email", registration: "New account registration",
  social_login_google: "Google sign-in", social_login_facebook: "Facebook sign-in", social_login_linkedin: "LinkedIn sign-in",
};
const featureCategories: Record<string, string> = {
  basic_reconciliation: "Reconciliation", gst_reconciliation: "Reconciliation", near_match_review: "Matching",
  auto_resolution: "Matching", saved_setups: "Workspace", report_email: "Reporting", registration: "Accounts",
  social_login_google: "Accounts", social_login_facebook: "Accounts", social_login_linkedin: "Accounts",
};
const featureDescriptions: Record<string, string> = {
  basic_reconciliation: "Compare general ledger, payroll, and other supported workbook data.",
  gst_reconciliation: "Reconcile GST invoice and return data using the existing GST workflow.",
  near_match_review: "Review suggested near-matches and record accept or reject decisions.",
  auto_resolution: "Apply enabled workspace rules to eligible reconciliation exceptions.",
  saved_setups: "Save and reuse column, key, and reconciliation configuration.",
  report_email: "Send generated reconciliation reports using the configured delivery transport.",
  registration: "Allow new accounts to be created, subject to deployment configuration.",
  social_login_google: "Allow sign-in through a configured Google OAuth provider.",
  social_login_facebook: "Allow sign-in through a configured Facebook OAuth provider.",
  social_login_linkedin: "Allow sign-in through a configured LinkedIn OAuth provider.",
};
const limitGroups = {
  upload: ["max_file_size_mb", "max_files_per_run", "max_rows_per_sheet", "max_rows_per_run"],
  reconciliation: ["jobs_per_day", "jobs_per_month", "concurrent_jobs"],
  workspace: ["storage_mb", "report_retention_days", "report_emails_per_day", "max_email_recipients"],
} as const;
function getFormatCategory(extension: string) {
  const key = extension.replace(/^\./, "").toLowerCase();
  if (["xlsx", "xls", "xlsm", "xlsb", "ods"].includes(key)) return "Spreadsheet";
  if (["csv", "tsv", "txt"].includes(key)) return "Text data";
  if (["json", "xml"].includes(key)) return "Structured data";
  return "Document";
}
const limitLabels: Record<string, string> = {
  max_file_size_mb: "Maximum upload size (MB)", max_files_per_run: "Files per run", max_rows_per_sheet: "Rows per sheet",
  max_rows_per_run: "Rows per run", jobs_per_day: "Runs per day", jobs_per_month: "Runs per month",
  concurrent_jobs: "Concurrent runs", storage_mb: "Workspace storage (MB)", report_retention_days: "Report retention (days)",
  report_emails_per_day: "Report emails per day", max_email_recipients: "Recipients per report",
};
const periods = [["today", "Today"], ["7d", "7 days"], ["30d", "30 days"], ["custom", "Custom"], ["all", "All time"]] as const;
const OPERATIONS_PAGE_SIZE = 10;
const adminTitles: Record<AdminTab, string> = {
  dashboard: "Platform dashboard", users: "User management", features: "Feature management", limits: "Usage & limits",
  insights: "Product insights", complaints: "Complaints", activity: "Audit & activity", email: "Email management",
  preview: "User experience preview", settings: "Admin settings",
};
type UserColumnKey = "id" | "username" | "name" | "email" | "role" | "status" | "runs" | "storage" | "activity" | "created" | "actions";
const userColumnDefs: Array<{ key: UserColumnKey; label: string; width: number }> = [
  { key: "id", label: "User ID", width: 200 }, { key: "username", label: "Username", width: 140 },
  { key: "name", label: "Full name", width: 160 }, { key: "email", label: "Email", width: 220 },
  { key: "role", label: "Role", width: 110 }, { key: "status", label: "Status", width: 110 },
  { key: "runs", label: "Runs", width: 85 }, { key: "storage", label: "Storage used", width: 115 },
  { key: "activity", label: "Last active", width: 145 }, { key: "created", label: "Date created", width: 145 },
  { key: "actions", label: "Actions", width: 95 },
];
const USER_GRID_PREFS_KEY = "recliq_admin_user_grid_v1";
type UserGridPreferences = { columns: UserColumnKey[]; widths: Partial<Record<UserColumnKey, number>>; density: "comfortable" | "compact"; wrap: boolean };
function readUserGridPreferences() {
  const defaults: UserGridPreferences = { columns: userColumnDefs.map(({ key }) => key), widths: {}, density: "comfortable", wrap: true };
  try {
    const saved = JSON.parse(localStorage.getItem(USER_GRID_PREFS_KEY) || "null");
    if (!saved || typeof saved !== "object") return defaults;
    const columns: UserColumnKey[] = Array.isArray(saved.columns) ? saved.columns.filter((key: unknown): key is UserColumnKey => typeof key === "string" && userColumnDefs.some((column) => column.key === key)) : defaults.columns;
    const widths: Partial<Record<UserColumnKey, number>> = saved.widths && typeof saved.widths === "object" ? saved.widths : defaults.widths;
    return { columns: columns.length ? columns : defaults.columns, widths, density: saved.density === "compact" ? "compact" as const : "comfortable" as const, wrap: saved.wrap !== false };
  } catch { return defaults; }
}

type PendingAction =
  | { kind: "status"; user: AdminUser; status: "active" | "restricted" | "suspended" }
  | { kind: "command"; userId: string; username: string; command: "revoke" | "reset" | "delete" };

export function AdminPage({ section, userIdFilter = "", onNavigate }: { section: AdminTab; userIdFilter?: string; onNavigate: (section: AdminSection, userId?: string) => void }) {
  const adminTab = section;
  const [period, setPeriod] = useState<"today" | "7d" | "30d" | "custom" | "all">("7d");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [policy, setPolicy] = useState<AdminPolicy | null>(null);
  const [savedPolicy, setSavedPolicy] = useState<AdminPolicy | null>(null);
  const [availableFormats, setAvailableFormats] = useState<Array<{ extension: string; label: string; content_type: string }>>([]);
  const [health, setHealth] = useState<Record<string, { status: string; [key: string]: unknown }> | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [totalUsers, setTotalUsers] = useState(0);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [userSort, setUserSort] = useState("created_desc");
  const [limitGroup, setLimitGroup] = useState<"guest" | "registered">("guest");
  const [featureSearch, setFeatureSearch] = useState("");
  const [featureCategory, setFeatureCategory] = useState("");
  const [formatSearch, setFormatSearch] = useState("");
  const [formatCategory, setFormatCategory] = useState("");
  const [userPage, setUserPage] = useState(1);
  const [userPageSize, setUserPageSize] = useState(25);
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");
  const [activeFrom, setActiveFrom] = useState("");
  const [activeTo, setActiveTo] = useState("");
  const [runsMin, setRunsMin] = useState("");
  const [runsMax, setRunsMax] = useState("");
  const [storageMin, setStorageMin] = useState("");
  const [storageMax, setStorageMax] = useState("");
  const [userGridPrefs, setUserGridPrefs] = useState(readUserGridPreferences);
  const [activeResize, setActiveResize] = useState<{ key: UserColumnKey; startX: number; startWidth: number } | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [userDetailOpen, setUserDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [detailMessage, setDetailMessage] = useState("");
  const [emailRows, setEmailRows] = useState<Array<Record<string, unknown>>>([]);
  const [emailTotal, setEmailTotal] = useState(0);
  const [emailStatus, setEmailStatus] = useState("");
  const [emailOffset, setEmailOffset] = useState(0);
  const [emailSettings, setEmailSettings] = useState<PlatformEmailSettings | null>(null);
  const [emailSenderDraft, setEmailSenderDraft] = useState("");
  const [emailSettingsLoading, setEmailSettingsLoading] = useState(false);
  const [emailSettingsSaving, setEmailSettingsSaving] = useState(false);
  const [emailTestBusy, setEmailTestBusy] = useState(false);
  const [emailSettingsError, setEmailSettingsError] = useState("");
  const [emailSettingsMessage, setEmailSettingsMessage] = useState("");
  const [auditRows, setAuditRows] = useState<Array<Record<string, unknown>>>([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditAction, setAuditAction] = useState("");
  const [auditTarget, setAuditTarget] = useState("");
  const [auditSource, setAuditSource] = useState<"all" | "workspace" | "admin">("all");
  const [auditFrom, setAuditFrom] = useState("");
  const [auditTo, setAuditTo] = useState("");
  const [auditOffset, setAuditOffset] = useState(0);
  const [providers, setProviders] = useState<Array<{ id: string; label: string; enabled: boolean }>>([]);
  const [auditIntegrity, setAuditIntegrity] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [dialogReason, setDialogReason] = useState("");
  const [dialogError, setDialogError] = useState("");
  const confirmationRef = useRef<HTMLDialogElement>(null);
  const usersRequestSequence = useRef(0);
  const detailRequestSequence = useRef(0);
  const auditRequestSequence = useRef(0);
  const emailRequestSequence = useRef(0);

  async function reloadOverview() {
    if (period === "custom" && (!dateFrom || !dateTo)) return;
    try {
      setOverview(await api.adminOverview(period, dateFrom || undefined, dateTo || undefined));
      const healthResult = await api.adminHealth() as { services?: Record<string, { status: string; [key: string]: unknown }> };
      setHealth(healthResult.services ?? {});
    }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not load administrator overview."); }
  }
  async function reloadUsers() {
    const requestId = ++usersRequestSequence.current;
    setUsersLoading(true);
    try { const result = await api.adminUsers({ q: query.trim() || undefined, status: statusFilter || undefined, role: roleFilter || undefined, sort: userSort, created_from: createdFrom || undefined, created_to: createdTo || undefined, active_from: activeFrom || undefined, active_to: activeTo || undefined, runs_min: runsMin || undefined, runs_max: runsMax || undefined, storage_min: storageMin ? Number(storageMin) * 1024 * 1024 : undefined, storage_max: storageMax ? Number(storageMax) * 1024 * 1024 : undefined, page: userPage, page_size: userPageSize }); if (requestId === usersRequestSequence.current) { setUsers(result.users); setTotalUsers(result.total); } }
    catch (caught) { if (requestId === usersRequestSequence.current) setError(caught instanceof Error ? caught.message : "Could not load accounts."); }
    finally { if (requestId === usersRequestSequence.current) setUsersLoading(false); }
  }
  async function reloadPolicy() {
    try { const result = await api.adminPolicy(); const next = { features: result.features, limits: result.limits, email_ready: result.email_ready }; setPolicy(next); setSavedPolicy(next); setAvailableFormats(result.available_formats); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not load global policy."); }
  }
  async function reloadDetail(id: string) {
    const requestId = ++detailRequestSequence.current;
    if (!id) { setDetail(null); setDetailError(""); setDetailLoading(false); return; }
    setDetail(null);
    setDetailError("");
    setDetailLoading(true);
    try { const result = await api.adminUserDetail(id) as UserDetail; if (requestId === detailRequestSequence.current) setDetail(result); }
    catch (caught) { if (requestId === detailRequestSequence.current) setDetailError(caught instanceof Error ? caught.message : "Could not load account details."); }
    finally { if (requestId === detailRequestSequence.current) setDetailLoading(false); }
  }
  async function reloadAudit() {
    const requestId = ++auditRequestSequence.current;
    try {
      const result = await api.platformActivity({ action: auditAction.trim() || undefined, q: auditTarget.trim() || undefined, source: auditSource, date_from: auditFrom || undefined, date_to: auditTo || undefined, offset: auditOffset, limit: OPERATIONS_PAGE_SIZE });
      if (requestId === auditRequestSequence.current) { setAuditRows(result.events); setAuditTotal(result.total); }
    } catch (caught) { if (requestId === auditRequestSequence.current) setError(caught instanceof Error ? caught.message : "Could not load administrative activity."); }
  }
  async function reloadEmailDeliveries() {
    const requestId = ++emailRequestSequence.current;
    try {
      const result = await api.adminEmailDeliveries({ status: emailStatus || undefined, offset: emailOffset, limit: OPERATIONS_PAGE_SIZE }) as { deliveries: Array<Record<string, unknown>>; total: number };
      if (requestId === emailRequestSequence.current) { setEmailRows(result.deliveries); setEmailTotal(result.total); }
    } catch (caught) { if (requestId === emailRequestSequence.current) setError(caught instanceof Error ? caught.message : "Could not load report delivery history."); }
  }
  async function reloadEmailSettings() {
    setEmailSettingsLoading(true);
    setEmailSettingsError("");
    try {
      const result = await api.adminEmailSettings();
      setEmailSettings(result);
      setEmailSenderDraft(result.sender_email);
    } catch (caught) { setEmailSettingsError(caught instanceof Error ? caught.message : "Could not load email settings."); }
    finally { setEmailSettingsLoading(false); }
  }

  useEffect(() => { if (adminTab === "dashboard") void reloadOverview(); }, [adminTab, period, dateFrom, dateTo]);
  useEffect(() => { if (adminTab !== "users") return; const timer = window.setTimeout(() => { void reloadUsers(); }, 300); return () => window.clearTimeout(timer); }, [adminTab, query, statusFilter, roleFilter, userSort, userPage, userPageSize, createdFrom, createdTo, activeFrom, activeTo, runsMin, runsMax, storageMin, storageMax]);
  useEffect(() => { if (["features", "limits", "users", "preview"].includes(adminTab)) void reloadPolicy(); if (adminTab === "features") void api.oauthProviders().then(setProviders).catch(() => undefined); }, [adminTab]);
  useEffect(() => { if (adminTab !== "activity") return; const timer = window.setTimeout(() => { void reloadAudit(); }, 200); return () => window.clearTimeout(timer); }, [adminTab, auditAction, auditTarget, auditSource, auditFrom, auditTo, auditOffset]);
  useEffect(() => { if (adminTab === "email") void reloadEmailDeliveries(); }, [adminTab, emailStatus, emailOffset]);
  useEffect(() => { if (adminTab === "email") void reloadEmailSettings(); }, [adminTab]);
  useEffect(() => { if (adminTab === "users") void reloadDetail(selectedId); }, [adminTab, selectedId]);
  useEffect(() => { if (adminTab !== "users") { setUserDetailOpen(false); setSelectedId(""); } }, [adminTab]);
  useEffect(() => { try { localStorage.setItem(USER_GRID_PREFS_KEY, JSON.stringify(userGridPrefs)); } catch { /* Preferences are optional and remain usable for this visit. */ } }, [userGridPrefs]);
  useEffect(() => { if (userIdFilter) setSelectedId(userIdFilter); }, [userIdFilter]);
  useEffect(() => { if (adminTab === "activity" && userIdFilter) setAuditTarget(userIdFilter); }, [adminTab, userIdFilter]);
  useEffect(() => {
    if (!activeResize) return;
    const move = (event: globalThis.PointerEvent) => setUserGridPrefs((current) => ({ ...current, widths: { ...current.widths, [activeResize.key]: Math.max(80, Math.min(520, activeResize.startWidth + event.clientX - activeResize.startX)) } }));
    const end = () => setActiveResize(null);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end, { once: true });
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", end); };
  }, [activeResize]);
  useEffect(() => {
    const dialog = confirmationRef.current;
    if (!dialog) return;
    if (pendingAction && !dialog.open) dialog.showModal();
    else if (!pendingAction && dialog.open) dialog.close();
  }, [pendingAction]);

  async function savePolicy() {
    if (!policy) return;
    setBusy(true); setError(""); setMessage("");
    try { const result = await api.updateAdminPolicy(policy) as AdminPolicy; setPolicy(result); setSavedPolicy(result); await reloadPolicy(); setMessage("Global policy saved."); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not save policy."); }
    finally { setBusy(false); }
  }

  function requestUserStatus(user: AdminUser, status: "active" | "restricted" | "suspended") {
    setDialogReason(""); setDialogError(""); setPendingAction({ kind: "status", user, status });
  }

  function requestAccountCommand(command: "revoke" | "reset" | "delete") {
    if (!detail) return;
    setDialogReason(""); setDialogError(""); setPendingAction({ kind: "command", command, userId: detail.user.id, username: detail.user.username });
  }

  async function confirmPendingAction() {
    if (!pendingAction) return;
    if (dialogReason.trim().length < 5) {
      setDialogError("Add an audit reason with at least five characters.");
      return;
    }
    setBusy(true); setDialogError("");
    try {
      if (pendingAction.kind === "status") {
        await api.updateAdminUserStatus(pendingAction.user.id, pendingAction.status, dialogReason.trim());
        setDetailMessage(`${pendingAction.user.username} is now ${pendingAction.status}.`);
        await reloadUsers(); await reloadDetail(pendingAction.user.id);
      } else {
        const { command, userId, username } = pendingAction;
        const wording = command === "revoke" ? "Active sessions revoked" : command === "reset" ? "Password reset required" : "Account access marked deleted";
        if (command === "revoke") await api.adminRevokeSessions(userId, dialogReason.trim());
        else if (command === "reset") await api.adminRequirePasswordReset(userId, dialogReason.trim());
        else await api.adminDeleteUser(userId, dialogReason.trim());
        setDetailMessage(`${wording} for ${username}.`);
        await reloadUsers(); await reloadDetail(userId);
      }
      setPendingAction(null);
    } catch (caught) { setDialogError(caught instanceof Error ? caught.message : "Administrator action failed."); }
    finally { setBusy(false); }
  }

  async function saveUserPolicy() {
    if (!detail) return;
    setBusy(true); setDetailError(""); setDetailMessage("");
    try { await api.updateAdminUserPolicy(detail.user.id, { features: detail.feature_overrides, limits: detail.limit_overrides }); await reloadDetail(detail.user.id); setDetailMessage("Account-specific permissions and limits saved."); }
    catch (caught) { setDetailError(caught instanceof Error ? caught.message : "Could not save account policy."); }
    finally { setBusy(false); }
  }

  async function saveEmailSender(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (emailSettingsSaving) return;
    const sender = emailSenderDraft.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(sender)) {
      setEmailSettingsError("Enter a valid sender email address.");
      setEmailSettingsMessage("");
      return;
    }
    setEmailSettingsSaving(true);
    setEmailSettingsError("");
    setEmailSettingsMessage("");
    try {
      const result = await api.updateAdminEmailSettings(sender);
      setEmailSettings(result);
      setEmailSenderDraft(result.sender_email);
      setEmailSettingsMessage("Sender address saved.");
    } catch (caught) { setEmailSettingsError(caught instanceof Error ? caught.message : "Sender address could not be saved."); }
    finally { setEmailSettingsSaving(false); }
  }

  async function sendEmailTest() {
    if (emailTestBusy || !emailSettings?.ready) return;
    setEmailTestBusy(true);
    setEmailSettingsError("");
    setEmailSettingsMessage("");
    try {
      const result = await api.sendAdminTestEmail();
      setEmailSettingsMessage(result.message);
    } catch (caught) { setEmailSettingsError(caught instanceof Error ? caught.message : "Test email could not be sent."); }
    finally { setEmailTestBusy(false); }
  }

  async function verifyAudit() {
    setBusy(true); setAuditIntegrity("");
    try { const result = await api.adminVerifyAudit(); setAuditIntegrity(result.valid ? `Valid: ${result.checked.toLocaleString()} events checked.` : `Integrity check failed at event ${result.broken_at}.`); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not verify audit integrity."); }
    finally { setBusy(false); }
  }

  function updateLimit(group: string, key: string, value: number) {
    setPolicy((current) => current ? { ...current, limits: { ...current.limits, [group]: { ...current.limits[group], [key]: value } } } : current);
  }
  function updateFormats(group: string, extension: string, enabled: boolean) {
    setPolicy((current) => {
      if (!current) return current;
      const allowed = new Set((current.limits[group]?.allowed_formats as string[] | undefined) ?? []);
      if (enabled) allowed.add(extension); else allowed.delete(extension);
      return { ...current, limits: { ...current.limits, [group]: { ...current.limits[group], allowed_formats: [...allowed].sort() } } };
    });
  }
  function updateUserFormat(extension: string, enabled: boolean) {
    if (!detail) return;
    const current = new Set((detail.limit_overrides.allowed_formats as string[] | undefined) ?? (detail.limits.allowed_formats as string[] | undefined) ?? []);
    if (enabled) current.add(extension); else current.delete(extension);
    setDetail({ ...detail, limit_overrides: { ...detail.limit_overrides, allowed_formats: [...current].sort() } });
  }
  function updateDetailLimit(event: ChangeEvent<HTMLInputElement>, key: string) {
    if (!detail) return;
    const value = Number(event.target.value);
    setDetail({ ...detail, limit_overrides: { ...detail.limit_overrides, [key]: Number.isFinite(value) ? value : 0 } });
  }

  const currentPageCount = Math.max(1, Math.ceil(totalUsers / userPageSize));
  const providerUnavailable = (name: string) => !providers.some((provider) => provider.id === name && provider.enabled);
  const policyDirty = Boolean(policy && savedPolicy && JSON.stringify(policy) !== JSON.stringify(savedPolicy));
  const shownUserColumns = userColumnDefs.filter(({ key }) => userGridPrefs.columns.includes(key));
  const filteredFeatures = Object.entries(featureLabels).filter(([key, label]) =>
    (!featureCategory || featureCategories[key] === featureCategory) &&
    `${label} ${featureCategories[key]} ${featureDescriptions[key]}`.toLowerCase().includes(featureSearch.trim().toLowerCase())
  );
  const filteredFormats = availableFormats.filter((format) =>
    (!formatCategory || formatCategory === getFormatCategory(format.extension)) &&
    `${format.label} ${format.extension} ${format.content_type} ${getFormatCategory(format.extension)}`.toLowerCase().includes(formatSearch.trim().toLowerCase())
  );
  const selectedFormats = (policy?.limits[limitGroup]?.allowed_formats as string[] | undefined) ?? [];
  const visibleFormatExtensions = filteredFormats.map((format) => format.extension);

  function setVisibleFormats(enabled: boolean) {
    const next = new Set(selectedFormats);
    visibleFormatExtensions.forEach((extension) => enabled ? next.add(extension) : next.delete(extension));
    if (!next.size) { setError("At least one supported upload format must remain enabled."); return; }
    setPolicy((current) => current ? { ...current, limits: { ...current.limits, [limitGroup]: { ...current.limits[limitGroup], allowed_formats: [...next].sort() } } } : current);
  }

  function toggleUserColumn(key: UserColumnKey, checked: boolean) {
    setUserGridPrefs((current) => {
      const columns = checked ? [...current.columns, key] : current.columns.filter((item) => item !== key);
      return { ...current, columns: columns.length ? columns : current.columns };
    });
  }

  function userCell(user: AdminUser, key: UserColumnKey): ReactNode {
    if (key === "id") return <code title={user.id}>{user.id}</code>;
    if (key === "username") return user.username;
    if (key === "name") return user.display_name || user.username;
    if (key === "email") return user.email;
    if (key === "role") return user.role.replace(/_/g, " ");
    if (key === "status") return <span className={`admin-status is-${user.status}`}>{user.status}</span>;
    if (key === "runs") return user.usage.reconciliation_count.toLocaleString();
    if (key === "storage") return formatBytes(user.usage.storage_bytes);
    if (key === "activity") return user.last_seen_at ? new Date(user.last_seen_at).toLocaleString() : "Never";
    if (key === "created") return new Date(user.created_at).toLocaleDateString();
    return <button type="button" className="secondary compact" onClick={(event) => { event.stopPropagation(); openUserDetail(user.id); }}>Manage</button>;
  }

  function openUserDetail(id: string) {
    setUserDetailOpen(true);
    setDetailError("");
    setDetailMessage("");
    if (selectedId !== id) {
      setDetail(null);
      setDetailLoading(true);
      setSelectedId(id);
    } else if (!detail && !detailLoading) {
      void reloadDetail(id);
    }
  }

  function closeUserDetail() {
    setUserDetailOpen(false);
    setDetailError("");
    setDetailMessage("");
  }

  function startColumnResize(event: ReactPointerEvent<HTMLSpanElement>, key: UserColumnKey) {
    event.preventDefault();
    const initial = userColumnDefs.find((column) => column.key === key)?.width ?? 120;
    setActiveResize({ key, startX: event.clientX, startWidth: userGridPrefs.widths[key] ?? initial });
  }

  function exportActivity(format: "csv" | "json") {
    const rows = auditRows.map((row) => ({
      occurred_at: row.occurred_at ?? "", source: row.source ?? "", actor_type: row.actor_type ?? "",
      actor_id: row.actor_id ?? "", scope_id: row.scope_id ?? "", action: row.action ?? "",
      target_type: row.target_type ?? "", target_id: row.target_id ?? "", summary: row.summary ?? "",
    }));
    const content = format === "json" ? JSON.stringify(rows, null, 2) : [
      Object.keys(rows[0] ?? { occurred_at: "" }).join(","),
      ...rows.map((row) => Object.values(row).map((value) => `"${String(value).replace(/"/g, '""')}"`).join(",")),
    ].join("\r\n");
    const blob = new Blob([content], { type: format === "json" ? "application/json" : "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = url; link.download = `recliq-platform-activity-page-${Math.floor(auditOffset / OPERATIONS_PAGE_SIZE) + 1}.${format}`; link.click();
    URL.revokeObjectURL(url);
  }

  return <section className="page admin-page">
    <div className="page-title"><div><span className="eyebrow">Restricted area</span><h1>{adminTitles[adminTab]}</h1><p>{adminTab === "dashboard" ? "Platform health, account activity, service usage, and incoming support." : "Platform-wide settings and activity. Workspace data remains private."}</p></div><button className="secondary" type="button" disabled={busy || emailSettingsLoading} onClick={() => { if (adminTab === "dashboard") void reloadOverview(); if (adminTab === "users") void reloadUsers(); if (["features", "limits"].includes(adminTab)) void reloadPolicy(); if (adminTab === "email") { void reloadEmailSettings(); void reloadEmailDeliveries(); } }}><RefreshCw size={16} />Refresh</button></div>
    {message && <p className="success-text" role="status">{message}</p>}{error && <p className="error-text" role="alert">{error}</p>}
    {adminTab === "dashboard" && <>
    <div className="admin-dashboard-filters"><div className="admin-period segmented-control" role="group" aria-label="Dashboard period">{periods.map(([value, label]) => <button key={value} type="button" className={period === value ? "is-active" : ""} aria-pressed={period === value} onClick={() => setPeriod(value as typeof period)}>{label}</button>)}</div>
      {period === "custom" && <div className="admin-date-range"><label>From<input type="date" value={dateFrom} max={dateTo || undefined} onChange={(event) => setDateFrom(event.target.value)} /></label><label>To<input type="date" value={dateTo} min={dateFrom || undefined} onChange={(event) => setDateTo(event.target.value)} /></label></div>}
    </div>
    <div className="admin-kpis">{overview ? <>
      <AdminKpi icon={<Users />} label="Accounts" value={overview.accounts.total} detail={`${overview.accounts.active} active · ${overview.accounts.active_in_period} active in period · ${overview.accounts.new_in_period} new · ${overview.accounts.suspended} suspended · ${overview.accounts.restricted} restricted`} />
      <AdminKpi icon={<Activity />} label="Reconciliation jobs" value={overview.jobs.total} detail={`${overview.jobs.completed} completed · ${overview.jobs.failed} failed · ${overview.jobs.queued_or_processing} active · ${overview.jobs.registered} registered / ${overview.jobs.guest} guest`} />
      <AdminKpi icon={<Activity />} label="Completed job share" value={overview.jobs.completed + overview.jobs.failed ? `${Math.round(overview.jobs.completed / (overview.jobs.completed + overview.jobs.failed) * 100)}%` : "No data"} detail="Completed or completed-with-errors jobs divided by terminal jobs in the selected period." />
      <AdminKpi icon={<HardDrive />} label="Workspace storage" value={formatBytes(overview.files.bytes + overview.reports.bytes)} detail={`${overview.files.count.toLocaleString()} uploads · ${formatBytes(overview.files.bytes)} upload data · ${formatBytes(overview.reports.bytes)} reports`} />
      <AdminKpi icon={<FileText />} label="Generated reports" value={overview.reports.count} detail={`${formatBytes(overview.reports.bytes)} report data stored`} />
      <AdminKpi icon={<Mail />} label="Email delivery" value={`${overview.emails.sent ?? 0} sent`} detail={`${overview.emails.failed ?? 0} failed · ${overview.emails.logged_locally ?? 0} logged locally`} />
      <button className="admin-kpi admin-kpi-action" type="button" onClick={() => onNavigate("complaints")}><span><MessageSquareText size={20} /></span><small>Unresolved complaints</small><strong>{overview.complaints.unresolved.toLocaleString()}</strong><p>{overview.complaints.unread.toLocaleString()} unread now · {overview.complaints.new.toLocaleString()} new · {overview.complaints.total.toLocaleString()} created in period</p></button>
    </> : <p className="muted">Loading overview…</p>}</div>

    {overview && <div className="admin-trends-grid"><AdminTrend title="Reconciliations over time" points={overview.activity.jobs} emptyLabel="No reconciliation activity in this period." /><AdminTrend title="New accounts over time" points={overview.activity.accounts} emptyLabel="No account registrations in this period." /></div>}

    {overview && <section className="admin-section"><div className="section-heading"><div><h2>Support demand by area</h2><p>Open and resolved requests created during the selected period.</p></div><button type="button" className="secondary compact" onClick={() => onNavigate("complaints")}>Open complaints</button></div>{Object.keys(overview.complaints.by_category).length ? <div className="complaint-category-list">{Object.entries(overview.complaints.by_category).sort((a, b) => b[1] - a[1]).map(([key, count]) => <div key={key}><span>{categoryLabel(key)}</span><div className="category-meter" aria-hidden="true"><span style={{ width: `${Math.max(3, count / Math.max(1, ...Object.values(overview.complaints.by_category)) * 100)}%` }} /></div><strong>{count.toLocaleString()}</strong></div>)}</div> : <p className="muted">No support requests in this period.</p>}</section>}

    {overview && (overview.jobs.failed > 0 || overview.emails.failed > 0 || overview.complaints.urgent > 0) && <section className="admin-section admin-attention"><div className="section-heading"><div><h2>Needs attention</h2><p>Current operational issues and urgent support items.</p></div></div><div className="admin-attention-actions">{overview.jobs.failed > 0 && <button type="button" className="attention-link" onClick={() => { setAuditAction("job.failed"); onNavigate("activity"); }}><strong>{overview.jobs.failed.toLocaleString()} failed reconciliations</strong><span>Review platform activity</span></button>}{overview.emails.failed > 0 && <button type="button" className="attention-link" onClick={() => { setEmailStatus("failed"); onNavigate("email"); }}><strong>{overview.emails.failed.toLocaleString()} failed email deliveries</strong><span>Open email management</span></button>}{overview.complaints.urgent > 0 && <button type="button" className="attention-link is-urgent" onClick={() => { onNavigate("complaints"); }}><strong>{overview.complaints.urgent.toLocaleString()} urgent open complaints</strong><span>Open the support queue</span></button>}</div></section>}

    <section className="admin-section"><div className="section-heading"><div><h2>System health</h2><p>Service-level status only; document contents are not exposed.</p></div><Database size={19} /></div><div className="health-grid">{health ? Object.entries(health).map(([name, item]) => <div className="health-item" key={name}><span className={`health-dot is-${item.status}`} /><strong>{name}</strong><span>{item.status}</span>{Object.entries(item).filter(([key]) => key !== "status").map(([key, value]) => <small key={key}>{key}: {String(value)}</small>)}</div>) : <span className="muted">Checking services…</span>}</div></section>
    </>}

    {adminTab === "features" && <section className="admin-section"><div className="section-heading"><div><h2>Feature management</h2><p>Only capabilities enforced by the current product policy are listed. Account overrides cannot bypass a disabled global feature.</p></div><Shield size={19} /></div>
      {policy ? <>
        <div className="admin-feature-tools"><label className="search-field"><Search size={16} aria-hidden="true" /><span className="visually-hidden">Search features</span><input value={featureSearch} onChange={(event) => setFeatureSearch(event.target.value)} placeholder="Search feature name or description" />{featureSearch && <button className="icon-button" type="button" aria-label="Clear feature search" onClick={() => setFeatureSearch("")}><X size={15} /></button>}</label><label>Category<select value={featureCategory} onChange={(event) => setFeatureCategory(event.target.value)}><option value="">All categories</option>{[...new Set(Object.values(featureCategories))].map((category) => <option key={category}>{category}</option>)}</select></label></div>
        <div className="table-panel admin-feature-table"><div className="table-scroll-x"><table><thead><tr><th>Feature</th><th>Category</th><th>Availability</th><th>Enabled</th></tr></thead><tbody>{filteredFeatures.length ? filteredFeatures.map(([key, label]) => { const unavailableProvider = key.startsWith("social_login_") && providerUnavailable(key.slice("social_login_".length)); const unavailableEmail = key === "report_email" && !policy.email_ready; const unavailable = unavailableProvider || unavailableEmail; return <tr key={key}><td><strong>{label}</strong><small>{featureDescriptions[key]}</small></td><td>{featureCategories[key]}</td><td><span className={`admin-availability${unavailable ? " is-unavailable" : ""}`}>{unavailableProvider ? "Provider not configured" : unavailableEmail ? "Email not configured" : "Supported"}</span></td><td><label className="admin-switch"><input type="checkbox" role="switch" aria-label={`Enable ${label}`} checked={Boolean(policy.features[key])} disabled={unavailable} onChange={(event) => setPolicy({ ...policy, features: { ...policy.features, [key]: event.target.checked } })} /><span>{policy.features[key] ? "On" : "Off"}</span></label></td></tr>; }) : <tr><td colSpan={4} className="table-empty">No features match this search.</td></tr>}</tbody></table></div></div>
        {policyDirty && <p className="admin-unsaved-note" role="status">Unsaved policy changes. Preview them before saving; they apply only to future operations.</p>}
        <div className="admin-policy-actions"><button className="secondary" type="button" disabled={!policyDirty || busy} onClick={() => { if (savedPolicy) setPolicy(savedPolicy); }}>Discard changes</button><button className="secondary" type="button" onClick={() => onNavigate("preview")}>{policyDirty ? "Preview pending changes" : "Preview current policy"}</button><button className="primary" type="button" disabled={!policyDirty || busy} onClick={() => void savePolicy()}>Save feature settings</button></div>
      </> : <p className="muted">Loading policy…</p>}
    </section>}

    {adminTab === "limits" && <section className="admin-section"><div className="section-heading"><div><h2>Usage &amp; limits</h2><p>Configure one workspace policy at a time. Server-side upload and deployment ceilings remain in force.</p></div><HardDrive size={19} /></div>
      {policy ? <>
        <div className="admin-workspace-select"><label>Configure workspace<select value={limitGroup} onChange={(event) => setLimitGroup(event.target.value as typeof limitGroup)}><option value="guest">Guest users</option><option value="registered">Registered users</option></select></label><span>Editing {limitGroup === "guest" ? "guest workspace defaults" : "registered workspace defaults"}</span></div>
        <div className="admin-limit-sections">{Object.entries(limitGroups).map(([groupKey, keys]) => <section className="admin-limit-section" key={groupKey}><h3>{groupKey === "upload" ? "Upload limits" : groupKey === "reconciliation" ? "Reconciliation limits" : "Workspace and reporting"}</h3>{keys.map((key) => <label className="admin-limit-row" key={key}><span>{limitLabels[key]}<small>{limitDescription(key)}</small></span><input type="number" min={0} value={Number(policy.limits[limitGroup]?.[key] ?? 0)} onChange={(event) => updateLimit(limitGroup, key, Number(event.target.value))} /></label>)}</section>)}</div>
        <section className="admin-format-section"><div className="section-heading"><div><h3>Allowed upload formats</h3><p>These formats are derived from the backend parser registry.</p></div></div><div className="admin-format-tools"><label className="search-field"><Search size={16} aria-hidden="true" /><span className="visually-hidden">Search formats</span><input value={formatSearch} onChange={(event) => setFormatSearch(event.target.value)} placeholder="Search name, extension, or parser" />{formatSearch && <button className="icon-button" type="button" aria-label="Clear format search" onClick={() => setFormatSearch("")}><X size={15} /></button>}</label><label>Category<select value={formatCategory} onChange={(event) => setFormatCategory(event.target.value)}><option value="">All categories</option>{[...new Set(availableFormats.map((format) => getFormatCategory(format.extension)))].map((category) => <option key={category}>{category}</option>)}</select></label><button type="button" className="secondary compact" onClick={() => setVisibleFormats(true)}>Select visible</button><button type="button" className="secondary compact" onClick={() => setVisibleFormats(false)} disabled={selectedFormats.length <= visibleFormatExtensions.length && visibleFormatExtensions.every((extension) => selectedFormats.includes(extension))}>Deselect visible</button></div>
          <div className="table-panel admin-format-table"><div className="table-scroll-x"><table><thead><tr><th>Enabled</th><th>Format</th><th>Extension</th><th>Category</th><th>Parser</th><th>Description</th></tr></thead><tbody>{filteredFormats.map((format) => <tr key={format.extension}><td><input type="checkbox" aria-label={`Allow ${format.label}`} checked={selectedFormats.includes(format.extension)} disabled={selectedFormats.includes(format.extension) && selectedFormats.length === 1} onChange={(event) => updateFormats(limitGroup, format.extension, event.target.checked)} /></td><td>{format.label}</td><td><code>{format.extension}</code></td><td>{getFormatCategory(format.extension)}</td><td><span className="admin-availability">Available</span></td><td>{format.content_type || "Backend parser enabled"}</td></tr>)}{!filteredFormats.length && <tr><td colSpan={6} className="table-empty">No supported formats match this search.</td></tr>}</tbody></table></div></div>
        </section>
        {policyDirty && <p className="admin-unsaved-note" role="status">Unsaved policy changes. Existing files and completed jobs are not altered.</p>}
        <div className="admin-policy-actions"><button className="secondary" type="button" disabled={!policyDirty || busy} onClick={() => { if (savedPolicy) setPolicy(savedPolicy); }}>Discard changes</button><button className="secondary" type="button" onClick={() => onNavigate("preview")}>{policyDirty ? "Preview pending changes" : "Preview current policy"}</button><button className="primary" type="button" disabled={!policyDirty || busy} onClick={() => void savePolicy()}>Save usage limits</button></div>
      </> : <p className="muted">Loading policy…</p>}
    </section>}

    {adminTab === "insights" && <AdminProductInsights />}
    {adminTab === "complaints" && <AdminComplaintsPanel initialUserId={userIdFilter} />}
    {adminTab === "preview" && <AdminExperiencePreview policyOverride={policy ?? undefined} isDraft={policyDirty} />}

    {adminTab === "users" && <>
    <section className="admin-section"><div className="section-heading"><div><h2>Account directory</h2><p>{totalUsers.toLocaleString()} accounts match the current server-side filters. Account actions are audited; source documents and credentials are never exposed.</p></div></div>
      <div className="admin-user-filters"><label className="search-field"><Search size={16} aria-hidden="true" /><span className="visually-hidden">Search user ID, username, name, or email</span><input value={query} onChange={(event) => { setQuery(event.target.value); setUserPage(1); }} placeholder="Search ID, username, name, or email" />{query && <button className="icon-button" type="button" aria-label="Clear account search" onClick={() => { setQuery(""); setUserPage(1); }}><X size={15} /></button>}</label>
        <label>Status<select value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); setUserPage(1); }}><option value="">All statuses</option><option value="active">Active</option><option value="restricted">Restricted</option><option value="suspended">Suspended</option><option value="deleted">Deleted</option></select></label>
        <label>Role<select value={roleFilter} onChange={(event) => { setRoleFilter(event.target.value); setUserPage(1); }}><option value="">All roles</option><option value="user">User</option><option value="super_admin">Super Admin</option></select></label>
        <label>Sort<select value={userSort} onChange={(event) => { setUserSort(event.target.value); setUserPage(1); }}><option value="created_desc">Newest accounts</option><option value="created_asc">Oldest accounts</option><option value="username">Username</option><option value="activity_desc">Last active</option><option value="runs_desc">Most runs</option><option value="storage_desc">Most storage</option><option value="status">Status</option></select></label>
      </div>
      <details className="admin-filter-disclosure"><summary>Registration, activity, and usage filters</summary><div className="admin-advanced-filters"><label>Registered from<input type="date" value={createdFrom} max={createdTo || undefined} onChange={(event) => { setCreatedFrom(event.target.value); setUserPage(1); }} /></label><label>Registered to<input type="date" value={createdTo} min={createdFrom || undefined} onChange={(event) => { setCreatedTo(event.target.value); setUserPage(1); }} /></label><label>Active from<input type="date" value={activeFrom} max={activeTo || undefined} onChange={(event) => { setActiveFrom(event.target.value); setUserPage(1); }} /></label><label>Active to<input type="date" value={activeTo} min={activeFrom || undefined} onChange={(event) => { setActiveTo(event.target.value); setUserPage(1); }} /></label><label>Runs from<input type="number" min={0} value={runsMin} onChange={(event) => { setRunsMin(event.target.value); setUserPage(1); }} /></label><label>Runs to<input type="number" min={0} value={runsMax} onChange={(event) => { setRunsMax(event.target.value); setUserPage(1); }} /></label><label>Storage from (MB)<input type="number" min={0} value={storageMin} onChange={(event) => { setStorageMin(event.target.value); setUserPage(1); }} /></label><label>Storage to (MB)<input type="number" min={0} value={storageMax} onChange={(event) => { setStorageMax(event.target.value); setUserPage(1); }} /></label></div></details>
      <details className="admin-grid-preferences"><summary>Table columns and density</summary><div className="admin-grid-pref-controls"><fieldset><legend>Visible columns</legend>{userColumnDefs.map((column) => <label className="check-row" key={column.key}><input type="checkbox" checked={userGridPrefs.columns.includes(column.key)} disabled={userGridPrefs.columns.length === 1 && userGridPrefs.columns.includes(column.key)} onChange={(event) => toggleUserColumn(column.key, event.target.checked)} /><span>{column.label}</span></label>)}</fieldset><label>Row density<select value={userGridPrefs.density} onChange={(event) => setUserGridPrefs({ ...userGridPrefs, density: event.target.value as typeof userGridPrefs.density })}><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></label><label className="check-row"><input type="checkbox" checked={userGridPrefs.wrap} onChange={(event) => setUserGridPrefs({ ...userGridPrefs, wrap: event.target.checked })} /><span>Wrap cell text</span></label></div></details>
      <div className="admin-users-layout">
        <div className="table-panel admin-user-list">
          <div className="table-scroll-x">
            <table className={`admin-user-grid is-${userGridPrefs.density}${userGridPrefs.wrap ? " has-wrap" : " no-wrap"}`}>
              <colgroup>{shownUserColumns.map((column) => <col key={column.key} style={{ width: userGridPrefs.widths[column.key] ?? column.width }} />)}</colgroup>
              <thead><tr>{shownUserColumns.map((column) => <th key={column.key} scope="col">{column.label}<span role="separator" aria-orientation="vertical" aria-label={`Resize ${column.label} column`} aria-valuemin={80} aria-valuemax={520} aria-valuenow={userGridPrefs.widths[column.key] ?? column.width} title="Drag to resize; use arrow keys" tabIndex={0} className="column-resize-handle" onPointerDown={(event) => startColumnResize(event, column.key)} onKeyDown={(event) => { if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); const width = userGridPrefs.widths[column.key] ?? column.width; const delta = event.key === "ArrowRight" ? 10 : -10; setUserGridPrefs({ ...userGridPrefs, widths: { ...userGridPrefs.widths, [column.key]: Math.max(80, Math.min(520, width + delta)) } }); } }} /></th>)}</tr></thead>
              <tbody>{users.length ? users.map((user) => <tr key={user.id} className={selectedId === user.id ? "is-selected" : ""} aria-selected={selectedId === user.id} tabIndex={0} onClick={(event) => { event.currentTarget.focus(); openUserDetail(user.id); }} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openUserDetail(user.id); } }}>{shownUserColumns.map((column) => <td key={column.key} title={column.key === "id" ? user.id : column.key === "email" ? user.email : undefined}>{userCell(user, column.key)}</td>)}</tr>) : <tr><td colSpan={shownUserColumns.length} className="table-empty">No accounts match these filters.</td></tr>}</tbody>
            </table>
          </div>
          <div className="pagination"><label>Rows<select value={userPageSize} onChange={(event) => { setUserPageSize(Number(event.target.value)); setUserPage(1); }} aria-label="Rows per page"><option value={25}>25</option><option value={50}>50</option><option value={100}>100</option></select></label><span>{totalUsers ? `${((userPage - 1) * userPageSize + 1).toLocaleString()}–${Math.min(userPage * userPageSize, totalUsers).toLocaleString()} of ${totalUsers.toLocaleString()}` : "0 accounts"} · Page {userPage} of {currentPageCount}</span><div><button className="secondary compact" type="button" disabled={userPage <= 1} onClick={() => setUserPage((value) => value - 1)}>Previous</button><button className="secondary compact" type="button" disabled={userPage >= currentPageCount} onClick={() => setUserPage((value) => value + 1)}>Next</button></div></div>
        </div>
      </div>
      <AppModal open={userDetailOpen} title={detail?.user.display_name ?? (detailLoading ? "Loading account details" : "Account details")} eyebrow="Account detail" size="wide" onClose={closeUserDetail}>
        {detailMessage && <p className="success-text" role="status">{detailMessage}</p>}
        {detailError && <p className="error-text" role="alert">{detailError}</p>}
        {detailLoading && !detail && <p className="admin-detail-load-state" role="status">Loading account details…</p>}
        {!detail && !detailLoading && detailError && <div className="admin-detail-load-state"><button type="button" className="secondary" onClick={() => void reloadDetail(selectedId)}>Retry</button></div>}
        {!detail && !detailLoading && !detailError && <p className="muted">Select an account to view its details.</p>}
        {detail && <section className="admin-user-detail">
          <p className="admin-user-identity">{detail.user.username} · {detail.user.email}</p>
          <div className="admin-user-facts"><span>Status <strong>{detail.user.status}</strong></span><span>Role <strong>{detail.user.role}</strong></span><span>Email verified <strong>{detail.user.email_verified ? "Yes" : "No"}</strong></span><span>MFA <strong>{detail.user.mfa_enabled ? "Enabled" : "Not enabled"}</strong></span><span>Created <strong>{new Date(detail.user.created_at).toLocaleDateString()}</strong></span><span>Last active <strong>{detail.user.last_seen_at ? new Date(detail.user.last_seen_at).toLocaleString() : "Never"}</strong></span><span>Reconciliations <strong>{detail.usage.reconciliation_count}</strong></span><span>Total storage <strong>{formatBytes(detail.usage.storage_bytes)}</strong></span><span>Uploaded files <strong>{formatBytes(detail.usage.uploaded_bytes)}</strong></span><span>Reports and previews <strong>{formatBytes(detail.usage.report_bytes)}</strong></span></div>
          <div className="admin-action-row">{detail.user.status !== "active" && <button type="button" className="secondary" disabled={busy} onClick={() => { const user = users.find((item) => item.id === detail.user.id); if (user) requestUserStatus(user, "active"); }}>Reactivate</button>}{detail.user.status === "active" && <button type="button" className="secondary" disabled={busy} onClick={() => { const user = users.find((item) => item.id === detail.user.id); if (user) requestUserStatus(user, "restricted"); }}>Restrict</button>}{detail.user.status !== "suspended" && <button type="button" className="secondary" disabled={busy} onClick={() => { const user = users.find((item) => item.id === detail.user.id); if (user) requestUserStatus(user, "suspended"); }}>Suspend</button>}<button type="button" className="secondary" disabled={busy} onClick={() => requestAccountCommand("revoke")}>Force logout</button><button type="button" className="secondary" disabled={busy} onClick={() => requestAccountCommand("reset")}>Require password reset</button><button type="button" className="text-command is-danger" disabled={busy} onClick={() => requestAccountCommand("delete")}>Mark deleted</button><button type="button" className="secondary compact" onClick={() => { closeUserDetail(); onNavigate("complaints", detail.user.id); }}>View complaints ({detail.complaints})</button><button type="button" className="secondary compact" onClick={() => { setAuditTarget(detail.user.id); setAuditOffset(0); closeUserDetail(); onNavigate("activity"); }}>View platform activity</button></div>
          <div className="admin-user-policy"><h4>Account-specific restrictions</h4><p className="muted">Feature overrides can only restrict global access. Numeric quotas may be raised or lowered within deployment hard caps; allowed formats remain limited by the global policy.</p>{Object.entries(featureLabels).map(([key, label]) => <label className="check-row" key={key}><input type="checkbox" checked={detail.feature_overrides[key] !== false} disabled={!detail.features[key]} onChange={(event) => setDetail({ ...detail, feature_overrides: { ...detail.feature_overrides, [key]: event.target.checked } })} /><span>{label}{!detail.features[key] ? " · disabled globally" : ""}</span></label>)}{Object.entries(limitLabels).map(([key, label]) => <label className="admin-detail-limit" key={key}>{label}<input type="number" min={0} value={Number(detail.limit_overrides[key] ?? detail.limits[key] ?? 0)} onChange={(event) => updateDetailLimit(event, key)} /></label>)}<fieldset className="admin-format-fieldset"><legend>Allowed upload formats</legend><div className="admin-format-grid">{availableFormats.map((format) => <label className="check-row" key={format.extension}><input type="checkbox" disabled={!(policy?.limits.registered?.allowed_formats as string[] | undefined)?.includes(format.extension)} checked={((detail.limit_overrides.allowed_formats as string[] | undefined) ?? (detail.limits.allowed_formats as string[] | undefined) ?? []).includes(format.extension)} onChange={(event) => updateUserFormat(format.extension, event.target.checked)} /><span><strong>{format.extension}</strong><small>{format.label}</small></span></label>)}</div></fieldset><button type="button" className="secondary" disabled={busy} onClick={() => void saveUserPolicy()}>Save account restrictions</button></div>
          <div className="admin-user-events"><h4>Sign-in sessions</h4>{detail.sessions.length ? detail.sessions.slice(0, 8).map((session) => <p key={session.id}><strong>{session.user_agent || "Signed-in device"}</strong><small>Last active {new Date(session.last_seen_at).toLocaleString()} · Expires {new Date(session.expires_at).toLocaleString()}{session.revoked_at ? " · Revoked" : " · Active"}{session.ip_address ? ` · ${session.ip_address}` : ""}</small></p>) : <p className="muted">No sign-in sessions recorded.</p>}<h4>Recent admin events</h4>{detail.admin_events.length ? detail.admin_events.map((event) => <p key={event.id}><strong>{event.action}</strong><small>{new Date(event.occurred_at).toLocaleString()} · {event.result}</small></p>) : <p className="muted">No account-specific administrative events.</p>}<h4>Report email history</h4>{detail.report_emails.length ? detail.report_emails.slice(0, 5).map((email) => <p key={email.id}><strong>{email.status}</strong><small>{email.recipients.join(", ")} · {new Date(email.created_at).toLocaleString()}</small></p>) : <p className="muted">No report email deliveries.</p>}</div>
        </section>}
      </AppModal>
    </section>
    </>}

    {adminTab === "activity" && <section className="admin-section"><div className="section-heading"><div><h2>Platform audit &amp; activity</h2><p>Workspace and administrator events are combined for review. Export includes the filtered page and excludes source-file contents and secrets.</p></div><div className="admin-action-row"><button className="secondary" type="button" disabled={busy} onClick={() => void verifyAudit()}>Verify admin audit chain</button><button className="secondary" type="button" disabled={!auditRows.length} onClick={() => exportActivity("csv")}>Export CSV</button><button className="secondary" type="button" disabled={!auditRows.length} onClick={() => exportActivity("json")}>Export JSON</button></div></div>{auditIntegrity && <p className={auditIntegrity.startsWith("Valid") ? "success-text" : "error-text"}>{auditIntegrity}</p>}
      <div className="admin-activity-filters"><label>Search actor, user ID, username, email, target, or summary<input value={auditTarget} onChange={(event) => { setAuditOffset(0); setAuditTarget(event.target.value); }} placeholder="Search platform activity" /></label><label>Action or event type<input value={auditAction} onChange={(event) => { setAuditOffset(0); setAuditAction(event.target.value); }} placeholder="e.g. support.complaint_updated" /></label><label>Source<select value={auditSource} onChange={(event) => { setAuditOffset(0); setAuditSource(event.target.value as typeof auditSource); }}><option value="all">All activity</option><option value="workspace">Workspace</option><option value="admin">Administrator</option></select></label><label>From<input type="date" value={auditFrom} max={auditTo || undefined} onChange={(event) => { setAuditOffset(0); setAuditFrom(event.target.value); }} /></label><label>To<input type="date" value={auditTo} min={auditFrom || undefined} onChange={(event) => { setAuditOffset(0); setAuditTo(event.target.value); }} /></label></div>
      <div className="table-panel admin-activity-table"><div className="table-scroll-x"><table><thead><tr><th>Timestamp</th><th>Source</th><th>Actor</th><th>Actor ID</th><th>Workspace</th><th>Category</th><th>Action</th><th>Description</th><th>Related ID</th></tr></thead><tbody>{auditRows.length ? auditRows.map((event, index) => <tr key={String(event.event_id ?? index)}><td>{event.occurred_at ? new Date(String(event.occurred_at)).toLocaleString() : ""}</td><td>{String(event.source ?? "")}</td><td>{String(event.actor_type ?? "")}</td><td><code>{String(event.actor_id ?? "")}</code></td><td><code>{String(event.scope_id ?? "")}</code></td><td>{String(event.target_type ?? "")}</td><td><strong>{String(event.action ?? "Event")}</strong></td><td>{String(event.summary ?? "")}</td><td><code>{String(event.target_id ?? "")}</code></td></tr>) : <tr><td colSpan={9} className="table-empty">No activity matches these filters.</td></tr>}</tbody></table></div></div>
      <div className="pagination"><span>Page {Math.floor(auditOffset / OPERATIONS_PAGE_SIZE) + 1} of {Math.max(1, Math.ceil(auditTotal / OPERATIONS_PAGE_SIZE))} · {auditTotal.toLocaleString()} events</span><div><button type="button" className="secondary compact" disabled={auditOffset === 0} onClick={() => setAuditOffset((value) => Math.max(0, value - OPERATIONS_PAGE_SIZE))}>Previous</button><button type="button" className="secondary compact" disabled={auditOffset + OPERATIONS_PAGE_SIZE >= auditTotal} onClick={() => setAuditOffset((value) => value + OPERATIONS_PAGE_SIZE)}>Next</button></div></div>
    </section>}

    {adminTab === "email" && <>
      <section className="admin-section admin-email-settings"><div className="section-heading"><div><h2>Email sender</h2><p>Set the From address used for verification, password reset, and report emails.</p></div><Mail size={19} /></div>
        {emailSettingsLoading && <p className="muted" role="status">Loading email configuration…</p>}
        {emailSettings && <div className={`admin-email-transport is-${emailSettings.transport}`} role="status"><strong>{emailSettings.transport === "smtp" ? emailSettings.smtp_ready ? "SMTP delivery ready" : "SMTP needs configuration" : emailSettings.transport === "local_log" ? "Local log mode · no email is sent" : "Email delivery is disabled"}</strong><span>{emailSettings.transport === "local_log" ? "In local development, email contents and reset links are written to the backend log. They are not delivered to mailboxes." : emailSettings.smtp_ready ? "Test messages go to the verified email address of the signed-in Super Admin." : "Configure the SMTP transport in the backend environment. The sender address alone cannot deliver email."}</span></div>}
        {emailSettingsError && <p className="error-text" role="alert">{emailSettingsError}</p>}
        {emailSettingsMessage && <p className="success-text" role="status">{emailSettingsMessage}</p>}
        <form className="admin-email-form" noValidate onSubmit={(event) => void saveEmailSender(event)}>
          <label htmlFor="admin-email-sender">Sender email address<input id="admin-email-sender" type="email" autoComplete="email" maxLength={254} value={emailSenderDraft} onChange={(event) => { setEmailSenderDraft(event.target.value); setEmailSettingsError(""); setEmailSettingsMessage(""); }} required aria-describedby="admin-email-sender-help" /></label>
          <div className="admin-action-row"><button className="primary" type="submit" disabled={emailSettingsSaving || emailSettingsLoading || emailSenderDraft.trim() === emailSettings?.sender_email}>{emailSettingsSaving ? "Saving…" : "Save sender address"}</button><button className="secondary" type="button" disabled={emailTestBusy || emailSettingsLoading || !emailSettings?.ready || emailSettingsSaving} onClick={() => void sendEmailTest()}>{emailTestBusy ? "Sending…" : emailSettings?.transport === "local_log" ? "Write test to backend log" : "Send test email"}</button></div>
        </form>
        <p id="admin-email-sender-help" className="muted admin-email-help">SMTP host and authentication stay in backend environment secrets. For Gmail, use the matching account and a Google App Password; never enter a normal Google password here. The configured From address must be accepted by your mail provider.</p>
      </section>
      <section className="admin-section"><div className="section-heading"><div><h2>Report email delivery</h2><p>Review queued, processing, sent, and failed deliveries without exposing report contents.</p></div><Mail size={19} /></div>
      <label className="admin-delivery-filter">Delivery status<select value={emailStatus} onChange={(event) => { setEmailOffset(0); setEmailStatus(event.target.value); }}><option value="">All statuses</option><option value="queued">Queued</option><option value="processing">Processing</option><option value="sent">Sent</option><option value="failed">Failed</option></select></label>
      {emailRows.length ? emailRows.map((item, index) => <p className="admin-event-row" key={String(item.id ?? index)}><strong>{String(item.status ?? "")}{item.transport === "local_log" ? " · local log only" : item.transport === "smtp" ? " · SMTP" : ""}</strong><small>{Array.isArray(item.recipients) ? item.recipients.join(", ") : "Recipient metadata unavailable"} · {item.created_at ? new Date(String(item.created_at)).toLocaleString() : ""}</small></p>) : <p className="muted">No report email attempts match this status.</p>}
      <div className="pagination"><span>Page {Math.floor(emailOffset / OPERATIONS_PAGE_SIZE) + 1} of {Math.max(1, Math.ceil(emailTotal / OPERATIONS_PAGE_SIZE))} · {emailTotal} deliveries</span><div><button type="button" className="secondary compact" disabled={emailOffset === 0} onClick={() => setEmailOffset((value) => Math.max(0, value - OPERATIONS_PAGE_SIZE))}>Previous</button><button type="button" className="secondary compact" disabled={emailOffset + OPERATIONS_PAGE_SIZE >= emailTotal} onClick={() => setEmailOffset((value) => value + OPERATIONS_PAGE_SIZE)}>Next</button></div></div>
      </section>
    </>}
    <dialog ref={confirmationRef} className="admin-confirm-dialog" aria-labelledby="admin-confirm-title" aria-describedby="admin-confirm-description" onCancel={(event) => { event.preventDefault(); setPendingAction(null); setDialogError(""); }}>
      <div className="dialog-heading"><div><span className="eyebrow">Confirm administrator action</span><h2 id="admin-confirm-title">{pendingAction?.kind === "status" ? `${pendingAction.status === "active" ? "Reactivate" : pendingAction.status === "restricted" ? "Restrict" : "Suspend"} ${pendingAction.user.username}` : pendingAction?.command === "revoke" ? `Revoke sessions for ${pendingAction.username}` : pendingAction?.command === "reset" ? `Require password reset for ${pendingAction?.username}` : `Mark ${pendingAction?.kind === "command" ? pendingAction.username : "account"} deleted`}</h2></div><button type="button" className="icon-button" aria-label="Close confirmation" onClick={() => setPendingAction(null)}>×</button></div>
      <p id="admin-confirm-description">{pendingAction?.kind === "command" && pendingAction.command === "delete" ? "This revokes account access and marks the account deleted. Existing records are retained; no source files are opened or removed." : "This action affects account access and will be recorded in the administrator audit trail."}</p>
      <label htmlFor="admin-action-reason">Audit reason<AutoResizeTextarea id="admin-action-reason" rows={3} maxLength={500} required minLength={5} value={dialogReason} aria-invalid={Boolean(dialogError)} onChange={(event) => { setDialogReason(event.target.value); setDialogError(""); }} placeholder="Explain why this action is needed" /></label>
      {dialogError && <p className="field-error" role="alert">{dialogError}</p>}
      <div className="admin-dialog-actions"><button type="button" className="secondary" disabled={busy} onClick={() => setPendingAction(null)}>Cancel</button><button type="button" className={pendingAction?.kind === "command" && pendingAction.command === "delete" ? "danger-button" : "primary"} disabled={busy} onClick={() => void confirmPendingAction()}>{busy ? "Applying…" : "Confirm action"}</button></div>
    </dialog>
  </section>;
}

function AdminKpi({ icon, label, value, detail }: { icon: ReactNode; label: string; value: string | number; detail: string }) {
  return <article className="admin-kpi"><span>{icon}</span><small>{label}</small><strong>{typeof value === "number" ? value.toLocaleString() : value}</strong><p>{detail}</p></article>;
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let amount = value / 1024; let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
  return `${amount.toFixed(1)} ${units[unit]}`;
}

function limitDescription(key: string) {
  const descriptions: Record<string, string> = {
    max_file_size_mb: "Maximum accepted size for one uploaded file.", max_files_per_run: "Maximum workbooks accepted for one run.",
    max_rows_per_sheet: "Hard safety ceiling applied to each parsed sheet.", max_rows_per_run: "Combined row ceiling across one reconciliation.",
    jobs_per_day: "Runs available to one workspace in a calendar day.", jobs_per_month: "Runs available to one workspace in a calendar month.",
    concurrent_jobs: "Reconciliations allowed to execute simultaneously.", storage_mb: "Combined uploaded-file and report storage allowance.",
    report_retention_days: "Number of days reports are retained; zero follows deployment retention behavior.",
    report_emails_per_day: "Maximum report email attempts allowed each day.", max_email_recipients: "Maximum recipients accepted for one report email.",
  };
  return descriptions[key] ?? "Configured workspace limit enforced by the API.";
}

function categoryLabel(category: string) {
  const labels: Record<string, string> = {
    account_access: "Account access", file_upload: "File upload", reconciliation: "Reconciliation",
    matching_and_mapping: "Matching and mapping", results_and_reports: "Results and reports", saved_setups: "Saved setups",
    aliases: "Name aliases", auto_resolution: "Auto-resolution", learning: "Learning", performance: "Performance",
    privacy_security: "Privacy or security", other: "Other",
  };
  return labels[category] ?? category.replace(/_/g, " ");
}

function AdminTrend({ title, points, emptyLabel }: { title: string; points: Array<{ date: string; count: number }>; emptyLabel: string }) {
  const peak = Math.max(1, ...points.map((point) => point.count));
  return <section className="admin-section admin-trend"><div className="section-heading"><div><h2>{title}</h2><p>Daily counts from stored platform records.</p></div></div>{points.length ? <div className="admin-trend-chart" role="img" aria-label={`${title}: ${points.map((point) => `${point.date}, ${point.count}`).join("; ")}`} style={{ gridTemplateColumns: `repeat(${points.length}, minmax(14px, 1fr))` }}>{points.map((point) => <div className="admin-trend-point" key={point.date} title={`${point.date}: ${point.count}`}><strong>{point.count || ""}</strong><span style={{ height: `${Math.max(3, point.count / peak * 100)}%` }} /><small>{point.date.slice(5)}</small></div>)}</div> : <p className="admin-insights-state">{emptyLabel}</p>}</section>;
}
