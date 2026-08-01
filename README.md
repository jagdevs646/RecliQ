# RecliQ Single-User MVP

RecliQ, with the tagline “One click reconciliation,” is the browser-based version of the existing wxPython reconciliation application. It behaves like a desktop reconciliation tool in the browser: no login, no registration, no users, and no passwords. Each visitor receives an anonymous UUID session, and uploaded files, reconciliation jobs, history, and generated reports are isolated to that session.

## What Is Preserved

- Intelligent text, numeric, date, invoice, GSTIN/PAN, and identifier matching from `matchers.py`.
- Generic reconciliation with primary matching columns and one-to-one or many-to-many column rules.
- GST invoice reconciliation with duplicate invoice merging, amount comparison, confidence review, and Excel output.
- Horizontal data orientation support through internal dataframe transformation.

## Project Structure

```text
backend/
  app/api/routes/          FastAPI endpoints
  app/core/                settings and MVP constants
  app/database/            SQLAlchemy session and model metadata
  app/models/              ORM models
  app/schemas/             Pydantic request/response models
  app/services/            job and reconciliation orchestration
  app/storage/             local and Azure Blob storage implementations
  app/reconciliation/      GUI-free reconciliation engines
  alembic/                 database migrations
frontend/
  src/components/          reusable UI controls
  src/pages/               dashboard, upload, status, results, history
docker/                    container definitions
docs/                      architecture, API, install, deployment notes
tests/                     backend tests
```

## Quick Start With Docker

```bash
docker compose up --build
```

- Frontend: `http://localhost:5173`
- Backend: `http://localhost:8000`
- Health: `http://localhost:8000/health`

The app opens directly to the Dashboard.

## Local Development

See [docs/local-development.md](docs/local-development.md).

## API

See [docs/api.md](docs/api.md).

## Azure Deployment

See [docs/azure-deployment.md](docs/azure-deployment.md). For a new Azure Container Apps deployment, sign in with `az login`, then run `./scripts/deploy-azure.ps1 -SubscriptionId <your-subscription-id>` from the repository root. The script prompts for the PostgreSQL password and never writes it to source control.
