# RecliQ

**One click reconciliation.** RecliQ compares two sets of financial records (books against a bank statement, a ledger against a vendor statement, purchase invoices against GSTR-2B) and produces an Excel report of what matches, what differs, and what is missing on either side.

RecliQ supports anonymous guest workspaces and optional registered accounts. Guests continue without interruption; accounts provide a persistent workspace across browsers. Files, runs, reports, settings and history are always scoped to the active workspace.

## What it does

### Files in, report out
- **Reads** Excel (`.xlsx`, `.xls`), CSV/TSV/text, tables inside PDF and Word files, MT940, BAI2 and CAMT.053 bank statements, and GSTR-2B JSON from the GST portal. PDF tables that continue across pages are joined into one.
- **Several workbooks and sheets per run.** Pair each source sheet with its destination sheet; every pair gets its own rules.
- **Excel report** with a sheet for each outcome: Summary, Differences, Only in File 1, Only in File 2, Match Review, Matched, Checks, Sheet Rules and Auto-resolved. Report columns and formats can be customised before download.

### A six-step setup
1. **Upload files**: choose the source and destination workbooks.
2. **Pair sheets**: decide which sheet is compared with which.
3. **Matching key**: pick the column(s) that identify the same record in both files, with suggestions from RecliQ. Optional "must also match" columns, and keyless passes (same amount and a date within a window, or an amount within a range, optionally with similar narration) for records the key does not find.
4. **Map columns**: map the fields to compare, by drag and drop or row mapping, with a search box for long header lists. Add preparation steps (trim, remove prefixes, invert signs, debit/credit to a signed amount, rounding and more) and tolerances.
5. **Report setup**: choose which columns appear in the report.
6. **Run**: a data-quality pre-check flags blank keys, text in number columns, mismatched column types and keys that barely overlap before anything runs.

### Matching that handles real data
- **Similar keys**, not just identical ones: spacing, case and punctuation are ignored, and company names are compared after normalising legal forms ("Pvt Ltd", "Private Limited"), abbreviations and word order. Near matches are listed for you to confirm or reject.
- **Tolerances after matching**: accept small differences on matched records, e.g. amounts within ±5 or ±1%, dates within 3 days, or narration at least 75% similar. Accepted records move to Matched, with a comment saying why.
- **Date conventions**: day-first or month-first per rule; year-first dates are always read correctly.

### Reviewing results
- A results dashboard with counts per outcome and a preview of every report sheet. Columns can be resized, long comments wrapped, and near matches confirmed or rejected in place.
- **Change rules and run again**: reopen a finished run with its files and rules loaded, adjust anything and run again. Each run is kept separately in History.
- **Saved setups**: save a run's rules and reuse them on next period's files.

### Learning and control
- **Name aliases**: RecliQ suggests aliases (e.g. "IBM" = "International Business Machines") after you confirm the same pair in more than one run. Nothing is applied until you approve it.
- **Auto-resolution rules**: explain recurring exceptions (bank charges, TDS and so on) and assign a GL account; matching records are reported as auto-resolved.
- **Audit log**: an append-only record of uploads, runs, decisions, rule changes and deletions.
- **GST reconciliation**: a dedicated invoice flow with duplicate-invoice merging, amount comparison and confidence review.

### Optional accounts and administration
- Registration uses username, email verification and password; sign-in uses an HttpOnly session cookie. Guests can continue without an account.
- Google, Facebook and LinkedIn sign-in are integrated but remain unavailable until provider credentials are configured.
- Account settings include security sessions, MFA, theme and notifications. Guests retain local theme preferences.
- Super Admin access requires explicit provisioning and MFA. Feature policy, account restrictions, upload formats and quotas are enforced by the API.
- Guest workspace import is explicit and ownership-checked; it is never automatic.
- Report email is processed by the backend. `EMAIL_BACKEND=log` is local-only; real delivery requires SMTP configuration.

## Tech stack

| Part | Built with |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy, Alembic, pandas, pdfplumber |
| Frontend | React 18, TypeScript, Vite, Vitest |
| Data | SQLite for local work; PostgreSQL 16 in production |
| Jobs | In-process queue locally; Redis and a separate worker in production |
| Storage | Local disk or Azure Blob Storage |

## Project structure

```text
backend/
  app/api/routes/              FastAPI endpoints (files, analysis, reconciliation, jobs, reports, templates, aliases, resolution, learning, audit)
  app/reconciliation_engine/   matching engine: ingestion, normalisation, matching passes, tolerances, transformations, report builder
  app/reconciliation/          GST reconciliation and the original matchers
  app/services/                job, reconciliation, template, pre-check, alias, resolution and audit services
  app/models/  app/schemas/    database models and API request/response models
  app/jobs/                    durable job queue and worker
  app/storage/                 local and Azure Blob storage
  alembic/                     database migrations
frontend/
  src/pages/                   Dashboard, Reconcile wizard, Results, History, Saved setups, Name aliases, Auto-resolution, Learning, Audit log
  src/components/              wizard steps, mapping builder, rule editors, report customiser
  src/lib/                     plan building and validation (unit-tested, free of React)
docker/                        Dockerfiles for local and Azure images
docs/                          architecture, API, installation, operations and deployment guides
scripts/                       Azure deployment and CI helper scripts
tests/backend/                 backend test suite
```

## Run it locally

### Quick start: clone and run
The simplest way to try RecliQ on your own computer. It needs no Docker, database server or settings file: the backend uses a local SQLite file, keeps uploads in `backend/storage` and runs reconciliations in-process.

You need [Git](https://git-scm.com/downloads), [Python 3.12](https://www.python.org/downloads/) and [Node.js 22](https://nodejs.org/) (any version from 20.19 works).

**1. Get the code**

```bash
git clone https://github.com/jagdevs646/RecliQ.git
cd RecliQ
```

**2. Start the backend** (first terminal)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Leave it running. The API is up when the terminal shows `Application startup complete` (check http://localhost:8000/health).

**3. Start the frontend** (second terminal, from the `RecliQ` folder)

```bash
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm run dev
```

**4. Open RecliQ** at http://localhost:5173, or type `o` and press Enter in the frontend terminal to open it in your browser.

Next time, skip the install steps: activate the virtual environment and run `uvicorn app.main:app --reload` in `backend`, then `pnpm run dev` in `frontend`. Press `Ctrl+C` in each terminal to stop.

> If PowerShell says running scripts is disabled when you activate the virtual environment, use Command Prompt instead, or run `.venv\Scripts\python -m uvicorn app.main:app --reload` without activating it.

### With Docker
Starts the API, a job worker, Redis, PostgreSQL and the frontend:

```bash
docker compose up --build
```

- App: http://localhost:5173
- API: http://localhost:8000 (health check at `/health`)

### Without Docker
Backend (uses SQLite and an in-process job queue by default):

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

For SQLite, set `DATABASE_URL=sqlite:///./recliq_dev.db` and `JOB_QUEUE_BACKEND=local` in `.env`. More detail: [docs/local-development.md](docs/local-development.md).

Frontend:

```bash
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm run dev
```

Set `VITE_API_BASE_URL` (e.g. `http://localhost:8000/api`) if the API runs elsewhere.

## Tests

```bash
python -m pytest tests/backend          # backend, from the repository root
(cd backend && python -m pytest tests)  # account, support and security tests
cd frontend && pnpm typecheck && pnpm test
```

CI (`.github/workflows/ci.yml`) runs a secret scan, the backend tests, the database migrations on PostgreSQL (checking the audit log stays append-only), the frontend type-check, tests and build, dependency audits and the Docker image builds on every push.

## Configuration

Settings come from environment variables; see [backend/.env.example](backend/.env.example). Locally, the default `EMAIL_BACKEND=log` shows a one-time email-verification link after registration and writes action links to the API log; no email is sent. Production registration requires configured SMTP. See [backend account and administrator setup](backend/README.md#local-account-and-administrator-setup) for the local sign-in and first-admin steps. Main settings:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLite or PostgreSQL connection |
| `STORAGE_BACKEND`, `LOCAL_STORAGE_PATH`, `AZURE_STORAGE_*` | where uploads and reports are kept |
| `JOB_QUEUE_BACKEND`, `REDIS_URL` | `local` or `redis` job queue |
| `MAX_UPLOAD_MB`, `MAX_ROWS_PER_SHEET` | upload limits (50 MB by default) |
| `FRONTEND_ORIGIN` | allowed browser origin |
| `AUTH_CSRF_SECRET`, `AUTH_SESSION_DAYS`, `SESSION_COOKIE_SECURE` | CSRF signing, account-session duration and HTTPS-only cookies |
| `REGISTRATION_ENABLED`, `EMAIL_BACKEND`, `SMTP_*` | registration, account verification, password recovery and report delivery |
| `GOOGLE_CLIENT_ID/SECRET`, `FACEBOOK_CLIENT_ID/SECRET`, `LINKEDIN_CLIENT_ID/SECRET` | optional social login; disabled until credentials are configured |
| `BOOTSTRAP_ADMIN_USERNAME/EMAIL/PASSWORD/TOTP_SECRET` | secure first Super Admin provisioning; provide real secret values outside source control |
| `SENTRY_DSN`, `LOG_LEVEL`, `LOG_FORMAT` | error tracking and logging |

Secrets stay out of the repository: locally in `backend/.env` (ignored by git), and in production in the hosting platform's settings (Render's environment, or Container App secrets from the Azure script). Azure Key Vault is not used yet. CI's gitleaks scan fails the build if a secret is committed; see [docs/operations-and-security.md](docs/operations-and-security.md).

## Deployment

- **Azure Container Apps**: [docs/azure-deployment.md](docs/azure-deployment.md). After `az login`, run `./scripts/deploy-azure.ps1 -SubscriptionId <your-subscription-id>` from the repository root; it asks for the PostgreSQL password and never writes it to source control.
- **Render** (`render.yaml`) and **Vercel** (`frontend/vercel.json`) configurations are included for the API and the frontend.

## Documentation

| Guide | Covers |
|---|---|
| [Non-technical guide](docs/NON_TECHNICAL_GUIDE.md) | what RecliQ does, for business users |
| [Technical architecture](docs/TECHNICAL_ARCHITECTURE_GUIDE.md) and [architecture](docs/architecture.md) | how the system is built |
| [API](docs/api.md) | endpoints |
| [Installation](docs/installation.md) and [local development](docs/local-development.md) | setup |
| [Operations and security](docs/operations-and-security.md) | secrets, logging, limits, jobs, audit |

## License

Copyright © 2026 Jagdev Singh (RecliQ). All rights reserved.

RecliQ is proprietary software. The source code is visible here for reference only: no permission is granted to copy, modify, distribute, sublicense, or use it, in whole or in part, without prior written permission from the owner. See [LICENSE](LICENSE) for the full terms.
