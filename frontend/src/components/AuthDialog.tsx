import { ArrowLeft, LockKeyhole, ShieldCheck, X } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../services/api";
import type { AccountIdentity } from "../types";

const USERNAME_PATTERN = /^[A-Za-z0-9_.-]{3,64}$/;

type AuthMode = "login" | "register" | "forgot" | "resend" | "reset";

interface Props {
  onClose: () => void;
  onAuthenticated: (identity: AccountIdentity) => void;
  initialMode?: AuthMode;
  resetToken?: string;
  notice?: string;
  /** False when the server cannot create accounts (registration off or no verification email). */
  registrationOpen?: boolean;
}

export function AuthDialog({ onClose, onAuthenticated, initialMode = "login", resetToken = "", notice = "", registrationOpen = true }: Props) {
  const [mode, setMode] = useState<AuthMode>(resetToken ? "reset" : initialMode === "register" && !registrationOpen ? "login" : initialMode);
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [mfaRequired, setMfaRequired] = useState(false);
  const [message, setMessage] = useState(notice);
  const [error, setError] = useState("");
  const [verificationUrl, setVerificationUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [providers, setProviders] = useState<Array<{ id: string; label: string; enabled: boolean }>>([]);

  useEffect(() => {
    if (mode !== "login") return;
    api.oauthProviders().then(setProviders).catch(() => setProviders([]));
  }, [mode]);

  function changeMode(next: AuthMode) {
    setMode(next);
    setMfaRequired(false);
    setTotp("");
    setError("");
    setMessage("");
    setVerificationUrl("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const validEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
    if ((mode === "login" && (!username.trim() || !password))
      || (mode === "register" && (!USERNAME_PATTERN.test(username.trim()) || username.includes("@") || !displayName.trim() || !validEmail || password.length < 12))
      || ((mode === "forgot" || mode === "resend") && !validEmail)
      || (mode === "reset" && password.length < 12)
      || (mode === "login" && mfaRequired && !/^\d{6,8}$/.test(totp))) {
      setError(mode === "login" && mfaRequired
        ? "Enter the current 6–8 digit authenticator code."
        : mode === "register"
          ? "Check your username, display name, email address, and password. Passwords must contain at least 12 characters."
          : mode === "forgot" || mode === "resend"
            ? "Enter a valid email address."
            : mode === "reset"
              ? "Use a password with at least 12 characters."
              : "Enter your username and password.");
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (mode === "register") {
        const result = await api.register({ username: username.trim(), email, display_name: displayName, password });
        setMessage(result.message);
        setVerificationUrl(result.development_verification_url ?? "");
        setMode("login");
      } else if (mode === "login") {
        onAuthenticated(await api.login({ identifier: username, password, totp_code: mfaRequired ? (totp || undefined) : undefined }));
      } else if (mode === "forgot") {
        const result = await api.requestPasswordReset(email);
        setMessage(result.message);
        setVerificationUrl(result.development_reset_url ?? "");
      } else if (mode === "resend") {
        const result = await api.resendVerification(email);
        setMessage(result.message);
        setVerificationUrl(result.development_verification_url ?? "");
      } else {
        const result = await api.resetPassword(resetToken, password);
        setMessage(result.message);
        setMode("login");
      }
    } catch (caught) {
      if (mode === "login" && caught instanceof ApiError && caught.status === 428) {
        setMfaRequired(true);
        setTotp("");
        return;
      }
      const detail = caught instanceof ApiError ? caught.message : caught instanceof Error ? caught.message : "The request could not be completed.";
      setError(detail === "Account verification email is not configured."
        ? "Email verification is unavailable on this deployment. For local testing, restart the API with EMAIL_BACKEND=log; for a hosted site, the operator must configure SMTP."
        : detail);
    } finally {
      setBusy(false);
    }
  }

  const title = mode === "register" ? "Create your account" : mode === "forgot" ? "Reset your password" : mode === "resend" ? "Verify your email" : mode === "reset" ? "Choose a new password" : "Sign in to RecliQ";
  const usernameError = mode === "register" && username.includes("@")
    ? "Enter only your username, not your full email address. For example: jagdevs646."
    : mode === "register" && username.length >= 3 && !USERNAME_PATTERN.test(username)
      ? "Use 3–64 letters, numbers, dots, underscores, or hyphens."
      : "";

  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="auth-dialog" role="dialog" aria-modal="true" aria-labelledby="auth-title">
      <div className="auth-dialog-top">
        <div className="brand"><img src="/icon.ico" alt="" className="brand-logo" /><div><strong>RecliQ</strong><small>Private reconciliation workspace</small></div></div>
        <button type="button" className="icon-button" title="Close" aria-label="Close sign-in dialog" onClick={onClose}><X size={17} /></button>
      </div>
      <div className="auth-dialog-heading">
        <span className="eyebrow">Account access</span>
        <h2 id="auth-title">{title}</h2>
        <p>{mode === "register" ? "Your guest workspace stays separate until you choose to import it. Guest data is deleted after one day." : mode === "login" ? mfaRequired ? "Your credentials are verified. Enter your authenticator code to finish signing in." : "Sign in for a persistent workspace, or keep using RecliQ as a guest." : mode === "resend" ? "Verification links only confirm your email address. They do not change your password." : "We will send instructions if the address belongs to an account."}</p>
      </div>

      {mode === "login" && providers.some((provider) => provider.enabled) && <div className="oauth-buttons">
        {providers.filter((provider) => provider.enabled).map((provider) => <button key={provider.id} type="button" className="secondary" onClick={() => window.location.assign(api.oauthStartUrl(provider.id))}>
          <span className={`oauth-mark oauth-${provider.id}`} aria-hidden="true">{provider.label === "Google" ? "G" : provider.label === "Facebook" ? "f" : "in"}</span>Continue with {provider.label}
        </button>)}
      </div>}

      {mode === "login" && providers.some((provider) => provider.enabled) && <div className="auth-divider"><span>or use your account</span></div>}

      <form className="auth-form" noValidate onSubmit={submit}>
        {mode === "register" && <>
          <label>Username<input autoComplete="username" minLength={3} maxLength={64} pattern="[A-Za-z0-9_.-]{3,64}" title="Use 3–64 letters, numbers, dots, underscores, or hyphens; for example, jagdevs646." aria-invalid={Boolean(usernameError)} aria-describedby="register-username-help" value={username} onChange={(event) => setUsername(event.target.value)} required /><small id="register-username-help" className={usernameError ? "username-error" : undefined} aria-live="polite">{usernameError || "Use 3–64 letters, numbers, dots, underscores, or hyphens. Example: jagdevs646."}</small></label>
          <label>Display name<input autoComplete="name" maxLength={120} value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></label>
          <label>Email address<input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>
          <label>Password<input type="password" autoComplete="new-password" minLength={12} value={password} onChange={(event) => setPassword(event.target.value)} required /><small>Use at least 12 characters.</small></label>
        </>}
        {mode === "login" && <>
          <label>Username or email<input autoComplete="username" value={username} onChange={(event) => { setUsername(event.target.value); setMfaRequired(false); setTotp(""); }} required /></label>
          <label>Password<input type="password" autoComplete="current-password" value={password} onChange={(event) => { setPassword(event.target.value); setMfaRequired(false); setTotp(""); }} required /></label>
          {mfaRequired && <label>Authenticator code<input autoFocus inputMode="numeric" autoComplete="one-time-code" minLength={6} maxLength={8} value={totp} onChange={(event) => setTotp(event.target.value.replace(/\D/g, ""))} required /><small>Enter the current 6-digit code from your authenticator app.</small></label>}
        </>}
        {(mode === "forgot" || mode === "resend") && <label>Email address<input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>}
        {mode === "forgot" && <p className="muted auth-email-help">If the address belongs to an account, the reset instructions go to that address. Local development displays a test link here instead of sending email.</p>}
        {mode === "reset" && <label>New password<input type="password" autoComplete="new-password" minLength={12} value={password} onChange={(event) => setPassword(event.target.value)} required /><small>Use at least 12 characters.</small></label>}
        {message && <p className="success-text" role="status"><ShieldCheck size={16} />{message}</p>}
        {verificationUrl && <a className="secondary full-width auth-verify-link" href={verificationUrl}>{mode === "forgot" ? "Open password reset form" : "Verify email now"}</a>}
        {error && <p className="error-text" role="alert">{error}</p>}
        <button type="submit" className="primary full-width" disabled={busy}>{busy ? "Please wait…" : mode === "register" ? "Create account" : mode === "forgot" ? "Send reset instructions" : mode === "resend" ? "Send verification link" : mode === "reset" ? "Update password" : mfaRequired ? "Verify and sign in" : "Sign in"}</button>
      </form>
      <div className="auth-dialog-footer">
        {mode === "login" && <><span><button className="text-command" type="button" onClick={() => changeMode("forgot")}>Forgot password?</button> · <button className="text-command" type="button" onClick={() => changeMode("resend")}>Resend verification</button></span>{registrationOpen ? <span>New to RecliQ? <button className="text-command" type="button" onClick={() => changeMode("register")}>Create account</button></span> : <span>New accounts are not open yet.</span>}</>}
        {mode === "resend" && <button className="text-command" type="button" onClick={() => changeMode("forgot")}>Need to change your password? Request a reset</button>}
        {mode !== "login" && <button className="text-command" type="button" onClick={() => changeMode("login")}><ArrowLeft size={15} /> Back to sign in</button>}
      </div>
      <p className="auth-guest-note"><LockKeyhole size={14} />Guest mode remains available without an account.</p>
    </section>
  </div>;
}
