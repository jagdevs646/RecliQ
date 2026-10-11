# RecliQ Backend

FastAPI backend for RecliQ. Visitors can work anonymously in an isolated UUID session or optionally register and sign in to a persistent account workspace. Anonymous data remains scoped to the guest session; account data is scoped to `user-<UUID>` and resolved from an opaque, database-backed HttpOnly session cookie. A guest `X-Session-ID` can never override an authenticated account workspace.

Cookie-authenticated state-changing requests also require the CSRF token returned by `/api/auth/me`. Set `SESSION_COOKIE_SECURE=true` over HTTPS. The local default is `false` so the cookie works on `http://localhost`.

For local account testing, the default `EMAIL_BACKEND=log` writes action links to the API log and exposes one-time verification and password-reset links in the browser; no email is sent. This transport works only when `ENVIRONMENT` is `development`, `local` or `test`. `backend/.env` is loaded regardless of the directory used to launch Uvicorn. Production must explicitly configure SMTP credentials from a secret store. Google, Facebook and LinkedIn authentication remain disabled unless their provider credentials and redirect URLs are configured.

### Local account and administrator setup

1. Copy `.env.example` to `.env` in this `backend` directory. Keep `ENVIRONMENT=development` and `EMAIL_BACKEND=log` for local testing.
2. Start the API. Register with a username such as `jagdevs646` (do not put the email address in the Username field), open the **Verify email now** link shown after registration, then sign in. The email address can also be used at sign-in. For a forgotten password, choose **Forgot password?**; local log mode shows an **Open password reset form** link and does not send an email.
3. To provision the first Super Admin, choose a username and email not already used by an account, then set all four `BOOTSTRAP_ADMIN_*` values in `backend/.env`: username, email, a strong password, and a base32 TOTP secret. Generate a TOTP secret locally with `python -c "import pyotp; print(pyotp.random_base32())"`; add that secret to an authenticator app. Never commit or share these values.
4. Change the platform sender under **Administration → Email management → Email sender**. SMTP host and credentials remain backend secrets. For Gmail, enable 2-Step Verification and create a Google App Password; set `EMAIL_BACKEND=smtp`, `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USERNAME`, and `SMTP_PASSWORD`. The default sender is `jagdev.s65@gmail.com`; use **Send test email** in Email management to verify delivery. Local log mode writes messages and one-time links to the API log only.
5. Restart the API. On startup it creates the Super Admin if no administrator already exists. Sign in with that username/password and the current 6-digit authenticator code. The **Administration** navigation item then appears; administrative APIs also enforce administrator role and MFA server-side.

There is no default administrator and public registration can never grant an admin role. If a Super Admin already exists, bootstrap settings will not replace it. Production bootstrap values must be supplied through the deployment secret store, not `.env`.

The `20261009_0001` revision adds account and policy tables, `20261010_0001` widens storage counters, `20261011_0001` adds support tickets, and `20261011_0002` adds the editable platform email sender setting. Back up the production database and storage before manually applying migrations. No production migration is run as part of local development.

## Local Commands

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`.
