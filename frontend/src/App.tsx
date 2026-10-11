import { useEffect, useState } from "react";
import { AuthDialog } from "./components/AuthDialog";
import { Shell } from "./components/Shell";
import { api } from "./services/api";
import type { AccountIdentity, AdminSection, Job, Page, ThemeMode } from "./types";
import { AdminPage } from "./pages/AdminPage";
import { AliasesPage } from "./pages/AliasesPage";
import { AuditLogPage } from "./pages/AuditLogPage";
import { DashboardPage } from "./pages/DashboardPage";
import { HistoryPage } from "./pages/HistoryPage";
import { LearningPage } from "./pages/LearningPage";
import { ResolutionRulesPage } from "./pages/ResolutionRulesPage";
import { ResultsPage } from "./pages/ResultsPage";
import { SavedReconciliationsPage } from "./pages/SavedReconciliationsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { StatusPage } from "./pages/StatusPage";
import { UploadPage } from "./pages/UploadPage";
import { SupportPage } from "./pages/SupportPage";

interface AppRoute { page: Page; jobId?: string; templateId?: string; adminSection?: AdminSection; adminUserId?: string; notice?: string }
type AuthMode = "login" | "register" | "forgot" | "reset";
const adminSections: AdminSection[] = ["dashboard", "users", "features", "limits", "insights", "complaints", "activity", "email", "preview"];

function oauthNotice(params: URLSearchParams): string | undefined {
  const success = params.get("oauth");
  if (success === "success") return "You are signed in with your provider.";
  if (success === "linked") return "The sign-in provider was linked to your account.";
  if (success === "verification_sent") return "We sent a verification link to the email address from your provider. Verify it before signing in.";
  const errors: Record<string, string> = {
    verification: "Provider verification failed. Try signing in again.", code: "The provider did not return an authorization code.",
    profile: "The provider did not return a usable profile.", session: "Sign in before linking a provider.", already_linked: "That provider identity is already linked to another account.",
    unverified_email: "The provider did not confirm this email address. Verify it with the provider and try again.",
    verification_email_unavailable: "This provider requires an email verification link, but email delivery is not configured for this deployment.",
    verification_email: "Your account was created but the verification email could not be sent. Use Resend verification from sign-in after email delivery is available.",
    registration_disabled: "New account registration is currently disabled.", account_exists: "An account with that email already exists. Sign in and link the provider from Settings.",
    account_unavailable: "This account is not currently available.", provider: "Provider sign-in could not be completed.",
    mfa_required: "This account uses multi-factor authentication. Sign in with your password and authenticator code.",
    password_reset_required: "Reset the password for this account before continuing with provider sign-in.",
  };
  const code = params.get("oauth_error");
  return code ? errors[code] ?? "Provider sign-in failed." : undefined;
}

function readRoute(): AppRoute {
  const hash = window.location.hash.replace(/^#/, "") || "/dashboard";
  const [path, query = ""] = hash.split("?", 2);
  const parts = path.split("/").filter(Boolean);
  const params = new URLSearchParams(query);
  const pageName = parts[0] as Page | undefined;
  const allowed: Page[] = ["dashboard", "upload", "status", "results", "history", "saved", "aliases", "audit", "rules", "learning", "settings", "support", "admin"];
  const page = pageName && allowed.includes(pageName) ? pageName : "dashboard";
  return {
    page,
    jobId: (page === "results" || page === "status") ? parts[1] : undefined,
    templateId: page === "saved" ? parts[1] : undefined,
    adminSection: page === "admin" && adminSections.includes(parts[1] as AdminSection) ? parts[1] as AdminSection : "dashboard",
    adminUserId: page === "admin" ? params.get("user_id") ?? undefined : undefined,
    notice: oauthNotice(params),
  };
}

function guestIdentity(sessionId: string): AccountIdentity {
  return { authenticated: false, user: null, workspace: { type: "guest", id: sessionId, label: "Guest Workspace" } };
}

function writeRoute(path: string, replace = false) {
  const url = `#/${path.replace(/^\/+/, "")}`;
  if (replace) window.history.replaceState(null, "", url);
  else window.history.pushState(null, "", url);
}

export default function App() {
  const [route, setRoute] = useState<AppRoute>(() => readRoute());
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [rerunJob, setRerunJob] = useState<Job | null>(null);
  const [identity, setIdentity] = useState<AccountIdentity | null>(null);
  const [sessionId, setSessionId] = useState("");
  const [sessionReady, setSessionReady] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);
  const [authMode, setAuthMode] = useState<AuthMode>("login");
  const [resetToken, setResetToken] = useState("");
  const [notice, setNotice] = useState("");
  const [routeError, setRouteError] = useState("");
  const [theme, setTheme] = useState<ThemeMode>(() => {
    try { return (localStorage.getItem("recliq_theme") as ThemeMode) || "system"; } catch { return "system"; }
  });
  const [uploadType, setUploadType] = useState<"generic" | "gst">("generic");

  useEffect(() => {
    const onHashChange = () => setRoute(readRoute());
    window.addEventListener("hashchange", onHashChange);
    window.addEventListener("popstate", onHashChange);
    return () => { window.removeEventListener("hashchange", onHashChange); window.removeEventListener("popstate", onHashChange); };
  }, []);

  useEffect(() => {
    let alive = true;
    void (async () => {
      let sid = "";
      try { sid = await api.initializeSession(); } catch { /* The identity request below reports API availability. */ }
      if (alive) setSessionId(sid);

      const path = window.location.pathname.replace(/\/+$/, "");
      const token = new URLSearchParams(window.location.search).get("token") ?? "";
      if (token && path.endsWith("/verify-email")) {
        try { const result = await api.verifyEmail(token); if (alive) setNotice(result.message); }
        catch (error) { if (alive) setNotice(error instanceof Error ? error.message : "Email verification failed."); }
        if (alive) { window.history.replaceState(null, "", "/#/dashboard"); setRoute({ page: "dashboard" }); }
      } else if (token && path.endsWith("/verify-email-change")) {
        try { const result = await api.verifyEmailChange(token); if (alive) setNotice(result.message); }
        catch (error) { if (alive) setNotice(error instanceof Error ? error.message : "Email change verification failed."); }
        if (alive) { window.history.replaceState(null, "", "/#/dashboard"); setRoute({ page: "dashboard" }); }
      } else if (token && path.endsWith("/reset-password")) {
        if (alive) { setResetToken(token); setAuthMode("reset"); setAuthOpen(true); window.history.replaceState(null, "", "/#/dashboard"); setRoute({ page: "dashboard" }); }
      }

      try {
        const current = await api.identity();
        if (alive) {
          setIdentity(current);
          if (current.user?.theme) setTheme(current.user.theme);
        }
      } catch {
        if (alive) setIdentity(guestIdentity(sid));
      }
      if (alive) {
        const initial = readRoute();
        setRoute(initial);
        if (initial.notice) {
          setNotice(initial.notice);
          writeRoute(initial.page, true);
        }
        setSessionReady(true);
      }
    })();
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const applyTheme = () => {
      const resolved = theme === "system" ? (media.matches ? "dark" : "light") : theme;
      document.documentElement.dataset.theme = resolved;
      document.documentElement.style.colorScheme = resolved;
    };
    applyTheme();
    if (theme === "system") media.addEventListener("change", applyTheme);
    try { localStorage.setItem("recliq_theme", theme); } catch { /* Account preference still applies for this session. */ }
    return () => media.removeEventListener("change", applyTheme);
  }, [theme]);

  useEffect(() => {
    if (!route.notice) return;
    setNotice(route.notice);
    writeRoute(route.page, true);
  }, [route.notice]);

  useEffect(() => {
    if (!route.jobId || !identity) return;
    if (activeJob?.id === route.jobId) return;
    let alive = true;
    setRouteError("");
    void api.getJob(route.jobId).then((job) => { if (alive) setActiveJob(job); }).catch((error) => {
      if (alive) { setActiveJob(null); setRouteError(error instanceof Error ? error.message : "This reconciliation is not available in the current workspace."); }
    });
    return () => { alive = false; };
  }, [route.jobId, identity?.workspace.id, activeJob?.id]);

  useEffect(() => {
    if (!identity) return;
    const isAdmin = identity.user?.role === "super_admin" && identity.user.mfa_enabled;
    if (route.page === "admin" && !isAdmin) {
      setNotice("Administrator access is restricted to an authenticated Super Admin with MFA enabled.");
      setRoute({ page: "dashboard" });
      writeRoute("dashboard", true);
      return;
    }
    if (isAdmin && route.page !== "admin" && route.page !== "settings") {
      setRoute({ page: "admin", adminSection: "dashboard" });
      writeRoute("admin/dashboard", true);
    }
  }, [identity?.user?.role, identity?.user?.mfa_enabled, route.page]);

  useEffect(() => {
    if (!identity) return;
    const features = identity.features ?? {};
    if (route.page === "upload") {
      if (features.basic_reconciliation === false && features.gst_reconciliation === false) {
        setNotice("Reconciliation is currently unavailable for this workspace.");
        setRoute({ page: "dashboard" });
        writeRoute("dashboard", true);
      } else if (uploadType === "generic" && features.basic_reconciliation === false) {
        setUploadType("gst");
      } else if (uploadType === "gst" && features.gst_reconciliation === false) {
        setUploadType("generic");
      }
      return;
    }
    const requiredFeature: Partial<Record<Page, string>> = {
      saved: "saved_setups",
      aliases: "near_match_review",
      learning: "near_match_review",
      rules: "auto_resolution",
    };
    const feature = requiredFeature[route.page];
    if (feature && features[feature] === false) {
      setNotice("This feature is currently unavailable in this workspace.");
      setRoute({ page: "dashboard" });
      writeRoute("dashboard", true);
    }
  }, [identity, route.page, uploadType]);

  const currentIdentity = identity ?? guestIdentity(sessionId);
  const accountKey = `${currentIdentity.workspace.type}:${currentIdentity.workspace.id ?? sessionId}`;

  useEffect(() => {
    const labels: Record<Page, string> = {
      dashboard: "Dashboard", upload: "Reconcile", status: "Run status", results: "Results", history: "History",
      saved: "Saved setups", aliases: "Name aliases", audit: "Audit log", rules: "Auto-resolution", learning: "Learning",
      settings: currentIdentity.user?.role === "super_admin" ? "Admin Settings" : "Settings", support: "Support", admin: "Administration",
    };
    const adminLabels: Partial<Record<AdminSection, string>> = {
      dashboard: "Platform dashboard", users: "User management", features: "Feature management", limits: "Usage and limits",
      insights: "Product insights", complaints: "Complaints", activity: "Audit and activity", email: "Email management", preview: "User experience preview",
    };
    document.title = `${route.page === "admin" ? adminLabels[route.adminSection ?? "dashboard"] : labels[route.page]} — RecliQ`;
  }, [route.page, route.adminSection, currentIdentity.user?.role]);

  if (!sessionReady) {
    return <div className="app-loading" role="status" aria-live="polite"><img src="/icon.ico" alt="" className="app-loading-logo" /><strong>RecliQ</strong><span className="app-loading-bar" aria-hidden="true" /><small>Getting your workspace ready…</small></div>;
  }

  function go(path: string, page: Page) {
    writeRoute(path);
    setRoute({ page, jobId: page === "results" || page === "status" ? path.split("/")[1] : undefined, templateId: page === "saved" ? path.split("/")[1] : undefined });
    setNotice("");
  }

  function goAdmin(section: AdminSection, userId?: string) {
    const query = userId ? `?user_id=${encodeURIComponent(userId)}` : "";
    const path = `admin/${section}${query}`;
    writeRoute(path);
    setRoute({ page: "admin", adminSection: section, adminUserId: userId });
    setNotice("");
  }

  function navigate(page: Page) {
    if (page === "admin") { goAdmin("dashboard"); return; }
    if (currentIdentity.user?.role === "super_admin" && currentIdentity.user.mfa_enabled && page !== "settings") {
      goAdmin("dashboard"); return;
    }
    setRerunJob(null);
    setRouteError("");
    if (page === "upload") setUploadType(currentIdentity.features?.basic_reconciliation === false ? "gst" : "generic");
    go(page, page);
  }

  function openJob(job: Job) {
    setActiveJob(job);
    const page = ["completed", "completed_with_errors"].includes(job.status) ? "results" : "status";
    go(`${page}/${job.id}`, page);
  }

  function rerun(job: Job) {
    const requiredFeature = job.job_type === "gst" ? "gst_reconciliation" : "basic_reconciliation";
    if (currentIdentity.features?.[requiredFeature] === false) {
      setNotice("This reconciliation type is currently unavailable in this workspace.");
      return;
    }
    setRerunJob(job);
    setUploadType(job.job_type === "gst" ? "gst" : "generic");
    go("upload", "upload");
  }

  function openGst() {
    if (currentIdentity.features?.gst_reconciliation === false) {
      setNotice("GST reconciliation is currently unavailable in this workspace.");
      return;
    }
    setRerunJob(null); setUploadType("gst"); go("upload", "upload");
  }

  function changeIdentity(next: AccountIdentity) {
    setIdentity(next);
    setActiveJob(null);
    setRerunJob(null);
    setRouteError("");
    setAuthOpen(false);
    setResetToken("");
    if (next.user?.theme) setTheme(next.user.theme);
    if (next.user?.role === "super_admin" && next.user.mfa_enabled) goAdmin("dashboard");
    else go("dashboard", "dashboard");
  }

  async function signOut() {
    try { await api.logout(); } catch (error) { setNotice(error instanceof Error ? error.message : "Sign out could not be completed."); }
    try { changeIdentity(await api.identity()); }
    catch { changeIdentity(guestIdentity(sessionId)); }
  }

  async function refreshGuestIdentity() {
    try { changeIdentity(await api.identity()); }
    catch { changeIdentity(guestIdentity(sessionId)); }
  }

  return <>
    <Shell key={accountKey} activePage={route.page} activeAdminSection={route.adminSection ?? "dashboard"} identity={currentIdentity} onNavigate={navigate} onNavigateAdmin={(section) => goAdmin(section)} onSignIn={() => { setAuthMode("login"); setAuthOpen(true); }} onSignOut={() => void signOut()}>
      {notice && <div className="app-notice" role="status"><span>{notice}</span><button type="button" className="icon-button" title="Dismiss" aria-label="Dismiss notification" onClick={() => setNotice("")}>×</button></div>}
      {routeError && <div className="app-notice is-error" role="alert"><span>{routeError}</span><button type="button" className="icon-button" title="Dismiss" aria-label="Dismiss error" onClick={() => setRouteError("")}>×</button></div>}
      {route.page === "dashboard" && <DashboardPage onNavigateUpload={() => navigate("upload")} onNavigateGst={openGst} onNavigateHistory={() => navigate("history")} onNavigateSaved={() => navigate("saved")} onOpenJob={openJob} onRunSetup={(templateId) => go(`saved/${templateId}`, "saved")} />}
      {route.page === "upload" && <UploadPage key={`${rerunJob?.id ?? "new"}-${uploadType}`} initialType={rerunJob ? (rerunJob.job_type === "gst" ? "gst" : "generic") : uploadType} features={currentIdentity.features} rerunFrom={rerunJob} onStartFresh={() => setRerunJob(null)} onJobCreated={(job) => { setRerunJob(null); setActiveJob(job); openJob(job); }} />}
      {route.page === "status" && <StatusPage job={activeJob} onJobUpdate={setActiveJob} onViewResults={() => activeJob && go(`results/${activeJob.id}`, "results")} />}
      {route.page === "results" && <ResultsPage job={activeJob} canEmail={Boolean(currentIdentity.capabilities?.report_email)} canReview={currentIdentity.features?.near_match_review !== false} canSaveSetup={currentIdentity.features?.saved_setups !== false} canReconcile={currentIdentity.features?.basic_reconciliation !== false} onNewReconciliation={() => navigate("upload")} onChangeRules={() => { if (activeJob && currentIdentity.features?.basic_reconciliation !== false) { setRerunJob(activeJob); go("upload", "upload"); } }} />}
      {route.page === "history" && <HistoryPage onOpenJob={openJob} onRerunJob={rerun} canEmail={Boolean(currentIdentity.capabilities?.report_email)} />}
      {route.page === "saved" && <SavedReconciliationsPage initialTemplateId={route.templateId} onJobCreated={(job) => { setActiveJob(job); openJob(job); }} onNewReconciliation={() => navigate("upload")} />}
      {route.page === "aliases" && <AliasesPage />}
      {route.page === "audit" && <AuditLogPage />}
      {route.page === "rules" && <ResolutionRulesPage />}
      {route.page === "learning" && <LearningPage onNavigate={navigate} />}
      {route.page === "settings" && <SettingsPage identity={currentIdentity} theme={theme} onThemeChange={setTheme} onIdentityChange={setIdentity} onSignIn={() => { setAuthMode("login"); setAuthOpen(true); }} onAccountDeleted={() => void refreshGuestIdentity()} />}
      {route.page === "support" && <SupportPage />}
      {route.page === "admin" && currentIdentity.user?.role === "super_admin" && currentIdentity.user.mfa_enabled && <AdminPage section={route.adminSection ?? "dashboard"} userIdFilter={route.adminUserId} onNavigate={goAdmin} />}
    </Shell>
    {authOpen && <AuthDialog key={`${authMode}:${resetToken}`} initialMode={authMode} resetToken={resetToken} notice={notice} registrationOpen={currentIdentity.capabilities?.registration !== false} onClose={() => { setAuthOpen(false); setResetToken(""); setAuthMode("login"); }} onAuthenticated={changeIdentity} />}
  </>;
}
