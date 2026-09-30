# Local development

Set up the backend and frontend once with the manual steps in [installation.md](installation.md). This page covers daily work: running, testing, migrations and conventions.

## Running

Backend, from `backend/` with the virtual environment active:

```bash
uvicorn app.main:app --reload
```

With `JOB_QUEUE_BACKEND=local` (the default), runs execute in a thread pool inside the API process, so no worker or Redis is needed. To try the production queue, start Redis, set `JOB_QUEUE_BACKEND=redis` and `REDIS_URL`, and run a worker next to the API:

```bash
python -m app.jobs.worker
```

Frontend, from `frontend/`:

```bash
pnpm run dev
```

The dev server calls the API at `VITE_API_BASE_URL` (`http://localhost:8000/api` in `frontend/.env.development`). The backend must allow the frontend's address through `FRONTEND_ORIGIN`.

## Tests

| What | Command |
|---|---|
| Backend tests | `python -m pytest tests/backend` (from the repository root; install `backend/requirements-dev.txt` first) |
| Frontend type-check | `pnpm typecheck` (in `frontend/`) |
| Frontend tests | `pnpm test` (Vitest) |
| Frontend production build | `pnpm build` |

Backend tests use a temporary SQLite database and local storage, and call the API through FastAPI's test client, so they need no running services. `tests/backend/_helpers.py` has helpers to build workbooks, upload them and wait for a job. Frontend logic that can be tested without a browser lives in `frontend/src/lib` (plan building, column search, formats) with tests next to it.

CI runs the same commands, plus a secret scan, dependency audits, the migrations on PostgreSQL and the Docker builds; see [operations-and-security.md](operations-and-security.md#continuous-integration-githubworkflowsciyml).

## Database migrations

Schema changes go through Alembic:

```bash
cd backend
alembic revision -m "describe the change"    # write the upgrade and downgrade by hand
alembic upgrade head
alembic downgrade -1                         # check the downgrade works
```

Migrations must run on both SQLite and PostgreSQL. The audit log's triggers are created by a migration; never edit or delete audit rows, even in development data.

## Working with data

- A local SQLite database and `backend/storage` hold everything; delete both to start clean.
- Parsed sheets are cached next to each upload (`*.sheet-<hash>-r<version>.pkl`). Changes to how files are read must bump `_READER_VERSION` in `ingestion/extractor.py`, or old caches will be reused.
- Each browser is its own anonymous session. A private window starts a new, empty one.

## Conventions

- **Plans are the contract.** A new rule setting is added in `backend/app/schemas/reconciliation.py`, read by the engine, and produced by `frontend/src/lib/plan.ts` (`draftToSheetRule`, and its reverse `sheetRuleToDraft` for re-runs). Saved setups and re-runs then carry it automatically.
- **Messages are for finance users.** Errors and report comments say what happened and what to do, in plain words.
- **Frontend files use CRLF line endings**; keep them when editing.
- **Secrets never go in the code.** `backend/.env` is ignored by git; CI's gitleaks scan fails on anything that looks like a credential.
