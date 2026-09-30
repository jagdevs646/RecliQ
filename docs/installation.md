# Installation

Two ways to run RecliQ on your own machine: Docker, which starts the full production-like stack in one command, or a manual install, which is quicker to change and debug. For day-to-day development, see [local-development.md](local-development.md).

## Prerequisites

| For | You need |
|---|---|
| Docker install | Docker Desktop (or Docker Engine with Compose) |
| Manual install | Python 3.12, Node.js 22 with Corepack (for pnpm) |
| Optional | PostgreSQL 16 and Redis 7, if you want the production database and job queue without Docker |

## Docker install

```bash
git clone https://github.com/jagdevs646/RecliQ.git
cd RecliQ
docker compose up --build
```

This starts five containers:

| Service | Purpose | Address |
|---|---|---|
| `frontend` | The web app (Nginx) | http://localhost:5173 |
| `backend` | The API | http://localhost:8000 (docs at `/docs`) |
| `worker` | Runs reconciliation jobs from the queue | |
| `redis` | Job queue, with append-only persistence | |
| `postgres` | Database | localhost:5432 |

Uploads and reports are kept in the `backend-storage` volume, and the database in `postgres-data`. The passwords in `docker-compose.yml` are local placeholders; never reuse them for a real deployment.

## Manual install

Backend, from the repository root:

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
```

In `backend/.env`, set at least:

```env
DATABASE_URL=sqlite:///./recliq_dev.db     # or a PostgreSQL URL
STORAGE_BACKEND=local
LOCAL_STORAGE_PATH=./storage
JOB_QUEUE_BACKEND=local                    # runs jobs inside the API process
FRONTEND_ORIGIN=http://localhost:5173
```

Then create the database and start the API:

```bash
alembic upgrade head
uvicorn app.main:app --reload
```

Frontend, in a second terminal:

```bash
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm run dev
```

Open http://localhost:5173. The app opens on the Dashboard; there is no sign-up or login.

## Checking the install

- `http://localhost:8000/health` returns `{"status": "ok", …}`, and `/health/ready` confirms the database (and Redis, if used) is reachable.
- On **Reconcile**, **Download General sample template** gives a workbook showing the expected layout, to try a first run.

## Next steps

- Settings: [backend/.env.example](../backend/.env.example) lists every variable, and [operations-and-security.md](operations-and-security.md) explains them.
- Deploying to Azure: [azure-deployment.md](azure-deployment.md).
