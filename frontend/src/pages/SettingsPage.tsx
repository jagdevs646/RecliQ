import { AlertTriangle, KeyRound, Link2, LogOut, Mail, Monitor, Moon, Shield, Sun, Trash2, UploadCloud, UserRound } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import type { AccountIdentity, ThemeMode, WorkspaceOverview } from "../types";
import { api } from "../services/api";

interface Props {
  identity: AccountIdentity;
  theme: ThemeMode;
  onThemeChange: (theme: ThemeMode) => void;
  onIdentityChange: (identity: AccountIdentity) => void;
  onSignIn: () => void;
  onAccountDeleted: () => void;
}

interface LoginSession {
  id: string; created_at: string; last_seen_at: string; expires_at: string; user_agent: string | null; current: boolean;
}

export function SettingsPage({ identity, theme, onThemeChange, onIdentityChange, onSignIn, onAccountDeleted }: Props) {
  const user = identity.user;
  const [preferences, setPreferences] = useState<Record<string, boolean>>({});
  const [sessions, setSessions] = useState<LoginSession[]>([]);
  const [usage, setUsage] = useState<WorkspaceOverview["usage"] | null>(null);
  const [providers, setProviders] = useState<Array<{ id: string; label: string; enabled: boolean }>>([]);
  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [newEmail, setNewEmail] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [mfaSetup, setMfaSetup] = useState<{ secret: string; otpauth_url: string } | null>(null);
  const [mfaCode, setMfaCode] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setDisplayName(user?.display_name ?? "");
    api.workspaceOverview("all").then((overview) => setUsage(overview.usage)).catch(() => setUsage(null));
    api.oauthProviders().then(setProviders).catch(() => setProviders([]));
    if (user) {
      api.preferences().then((value) => {
        setPreferences(value.notifications);
        if (value.theme !== theme) onThemeChange(value.theme);
      }).catch(() => undefined);
      api.listSessions().then(setSessions).catch(() => undefined);
    }
  }, [identity.workspace.id]);

  async function saveDisplayName(event: FormEvent) {
    event.preventDefault();
    if (!displayName.trim()) { setMessage(""); setError("Enter a display name."); return; }
    setBusy(true); setMessage(""); setError("");
    try { onIdentityChange(await api.updateProfile(displayName)); setMessage("Display name updated."); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not update profile."); }
    finally { setBusy(false); }
  }

  async function saveEmail(event: FormEvent) {
    event.preventDefault();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(newEmail.trim())) { setMessage(""); setError("Enter a valid email address."); return; }
    if (!currentPassword) { setMessage(""); setError("Enter your current password to change email."); return; }
    setBusy(true); setMessage(""); setError("");
    try { const result = await api.changeEmail(newEmail, currentPassword); setMessage(result.message); setCurrentPassword(""); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not change email."); }
    finally { setBusy(false); }
  }

  async function savePassword(event: FormEvent) {
    event.preventDefault();
    if (!currentPassword || newPassword.length < 12) { setMessage(""); setError("Enter your current password and a new password with at least 12 characters."); return; }
    setBusy(true); setMessage(""); setError("");
    try { const result = await api.changePassword({ current_password: currentPassword, new_password: newPassword }); setMessage(result.message); setCurrentPassword(""); setNewPassword(""); setSessions(await api.listSessions()); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not change password."); }
    finally { setBusy(false); }
  }

  async function sendPasswordSetup() {
    if (!user) return;
    setBusy(true); setMessage(""); setError("");
    try { setMessage((await api.requestPasswordReset(user.email)).message); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not send password setup instructions."); }
    finally { setBusy(false); }
  }

  async function setTheme(next: ThemeMode) {
    onThemeChange(next);
    if (!user) {
      try { localStorage.setItem("recliq_theme", next); } catch { /* Appearance still applies for this page view. */ }
      return;
    }
    try { await api.updatePreferences({ theme: next, notifications: preferences }); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not save appearance preference."); }
  }

  async function setNotification(key: string, enabled: boolean) {
    const next = { ...preferences, [key]: enabled };
    setPreferences(next);
    try { await api.updatePreferences({ theme, notifications: next }); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not save notification preference."); }
  }

  async function importGuest() {
    if (!window.confirm("Import this browser's current guest workspace into your account? Existing guest records will move into this account. This cannot be undone.")) return;
    setBusy(true); setMessage(""); setError("");
    try { const result = await api.importGuestWorkspace(true); setMessage(`Guest workspace imported: ${Object.values(result.counts).reduce((sum, count) => sum + count, 0)} records moved.`); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "The guest workspace could not be imported."); }
    finally { setBusy(false); }
  }

  async function revokeSession(session: LoginSession) {
    if (!window.confirm(session.current ? "Revoke this session and sign out?" : "Sign this device out?")) return;
    try {
      await api.revokeSession(session.id);
      if (session.current) onAccountDeleted();
      else setSessions(await api.listSessions());
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not revoke session."); }
  }

  async function deleteAccount() {
    if (!window.confirm("Permanently disable access to this account? RecliQ will retain workspace records under its data-retention policy.")) return;
    setBusy(true); setError("");
    try { await api.deleteAccount("DELETE", currentPassword || undefined); onAccountDeleted(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not delete account."); }
    finally { setBusy(false); }
  }

  async function startMfa() {
    setBusy(true); setError("");
    try { setMfaSetup(await api.setupMfa()); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not start MFA setup."); }
    finally { setBusy(false); }
  }

  async function enableMfa(event: FormEvent) {
    event.preventDefault();
    if (!/^\d{6,8}$/.test(mfaCode)) { setError("Enter the current 6–8 digit authenticator code."); return; }
    setBusy(true); setError(""); setMessage("");
    try { await api.enableMfa(mfaCode); setMfaSetup(null); setMfaCode(""); setMessage("Authenticator protection enabled."); onIdentityChange(await api.identity()); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not enable MFA."); }
    finally { setBusy(false); }
  }

  const storageUsed = usage ? `${formatBytes(usage.storage_bytes)} of ${formatBytes(usage.storage_limit_bytes)}` : "Loading usage…";

  return <section className="page settings-page">
    <div className="page-title"><div><span className="eyebrow">Workspace</span><h1>Settings</h1><p>Account security, appearance, notifications and workspace usage.</p></div></div>
    {message && <p className="success-text" role="status">{message}</p>}{error && <p className="error-text" role="alert">{error}</p>}

    <section className="settings-section">
      <div className="settings-heading"><UserRound size={19} /><div><h2>Profile</h2><p>{user ? "Account identity and contact details." : "Guest workspaces stay in this browser, and their files and runs are deleted after one day."}</p></div></div>
      {user ? <div className="settings-grid">
        <form className="settings-form" noValidate onSubmit={saveDisplayName}><label>Username<input value={user.username} readOnly /></label><label>Email<input value={user.email} readOnly /></label><label>Display name<input maxLength={120} value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></label><button className="primary" type="submit" disabled={busy}>Save profile</button></form>
        <form className="settings-form" noValidate onSubmit={saveEmail}><h3>Change email</h3><label>New email<input type="email" value={newEmail} onChange={(event) => setNewEmail(event.target.value)} required /></label><label>Current password<input type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required /></label><button className="secondary" type="submit" disabled={busy}><Mail size={16} />Send verification link</button></form>
      </div> : <div className="settings-empty"><p>Sign in or create an account to keep a private workspace across browsers and devices.</p><button type="button" className="primary" onClick={onSignIn}>Sign in or create account</button></div>}
    </section>

    <section className="settings-section">
      <div className="settings-heading"><Monitor size={19} /><div><h2>Appearance</h2><p>Choose a workspace theme for this device{user ? " and sync it to your account" : ""}.</p></div></div>
      <div className="theme-options" role="group" aria-label="Appearance">
        {([ ["light", "Light", Sun], ["dark", "Dark", Moon], ["system", "System", Monitor] ] as const).map(([value, label, Icon]) => <button type="button" key={value} className={`theme-option ${theme === value ? "is-active" : ""}`} aria-pressed={theme === value} onClick={() => setTheme(value)}><Icon size={18} /><span>{label}</span></button>)}
      </div>
    </section>

    {user && <>
      <section className="settings-section">
        <div className="settings-heading"><Shield size={19} /><div><h2>Security</h2><p>Password, multi-factor protection, and active sign-in sessions.</p></div></div>
        <div className="settings-grid">
          {user.has_password ? <form className="settings-form" noValidate onSubmit={savePassword}><h3>Change password</h3><label>Current password<input type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required /></label><label>New password<input type="password" minLength={12} autoComplete="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required /></label><button type="submit" className="secondary" disabled={busy}><KeyRound size={16} />Update password</button></form> : <div className="settings-form"><h3>Create a password</h3><p>This account uses a sign-in provider. Set a password through the verified email recovery flow before using password-based account changes.</p><button type="button" className="secondary" onClick={() => void sendPasswordSetup()} disabled={busy}><Mail size={16} />Email password setup link</button></div>}
          <div className="settings-form"><h3>Authenticator app</h3><p>{user.mfa_enabled ? "MFA is enabled for this account." : "Add a time-based one-time code to sign-in."}</p>{!user.mfa_enabled && !mfaSetup && <button type="button" className="secondary" onClick={startMfa} disabled={busy}>Set up authenticator</button>}{mfaSetup && <form noValidate onSubmit={enableMfa}><p className="secret-code">{mfaSetup.secret}</p><p className="muted">Add this key to your authenticator app, then verify a current code.</p><label>6-digit code<input inputMode="numeric" autoComplete="one-time-code" maxLength={8} value={mfaCode} onChange={(event) => setMfaCode(event.target.value)} required /></label><button className="primary" type="submit" disabled={busy}>Enable MFA</button></form>}</div>
        </div>
        <div className="settings-subsection"><h3>Active sessions</h3>{sessions.length ? <div className="session-list">{sessions.map((session) => <div className="session-row" key={session.id}><div><strong>{session.current ? "This device" : session.user_agent || "Signed-in device"}</strong><small>Last active {formatDate(session.last_seen_at)} · Expires {formatDate(session.expires_at)}</small></div><button type="button" className="icon-button is-danger" title="Revoke session" aria-label="Revoke session" onClick={() => revokeSession(session)}><LogOut size={15} /></button></div>)}</div> : <p className="muted">No active sessions found.</p>}</div>
        <div className="settings-subsection"><h3>Link a sign-in provider</h3><div className="provider-link-row">{providers.filter((provider) => provider.enabled).map((provider) => <button key={provider.id} className="secondary" type="button" onClick={() => window.location.assign(api.oauthStartUrl(provider.id, "link"))}><Link2 size={15} />Link {provider.label}</button>)}{!providers.some((provider) => provider.enabled) && <p className="muted">No social sign-in providers are configured.</p>}</div></div>
      </section>

      <section className="settings-section">
        <div className="settings-heading"><Mail size={19} /><div><h2>Notifications</h2><p>Choose which account emails RecliQ may send.</p></div></div>
        {identity.capabilities?.email_notifications ? <div className="notification-options">
          {[["reconciliation_completed", "Reconciliation completed"], ["reconciliation_failed", "Reconciliation failed"], ["report_email_status", "Report email status"]].map(([key, label]) => <label className="check-row" key={key}><input type="checkbox" checked={Boolean(preferences[key])} onChange={(event) => setNotification(key, event.target.checked)} /><span>{label}</span></label>)}
        </div> : <p className="muted">Email notifications are unavailable until an email transport is configured.</p>}
      </section>

      <section className="settings-section">
        <div className="settings-heading"><UploadCloud size={19} /><div><h2>Workspace data</h2><p>Persistent workspace usage and your imported guest records.</p></div></div>
        <div className="usage-row"><span>Storage</span><strong>{storageUsed}</strong></div>
        <div className="usage-row"><span>Runs today</span><strong>{usage ? `${usage.jobs_today} of ${usage.jobs_per_day_limit}` : "Loading…"}</strong></div>
        <div className="usage-row"><span>Runs this month</span><strong>{usage ? `${usage.jobs_month} of ${usage.jobs_per_month_limit}` : "Loading…"}</strong></div>
        <div className="settings-actions"><button type="button" className="secondary" onClick={importGuest} disabled={busy}><UploadCloud size={16} />Import guest workspace</button><button type="button" className="text-command is-danger" onClick={deleteAccount} disabled={busy}><Trash2 size={16} />Disable account</button></div>
        <p className="muted settings-retention"><AlertTriangle size={14} />Disabling the account revokes sign-in. Stored reports are retained for the configured retention period.</p>
      </section>
    </>}
  </section>;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let amount = value / 1024;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
  return `${amount.toFixed(1)} ${units[unit]}`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "unknown" : date.toLocaleString();
}
