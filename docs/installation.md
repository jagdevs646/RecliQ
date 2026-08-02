# Installation

## Prerequisites

- Python 3.12
- Node.js 22
- PostgreSQL 16
- Docker Desktop for containerized development

## Manual Install

1. Install backend dependencies from `backend/requirements.txt`.
2. Copy `backend/.env.example` to `backend/.env`.
3. Set `DATABASE_URL` and storage settings.
4. Run `alembic upgrade head`.
5. Start `uvicorn app.main:app --reload`.
6. Install frontend dependencies with `corepack enable` followed by `pnpm install --frozen-lockfile`.
7. Start `pnpm run dev`.

The MVP opens directly to Dashboard and does not require login or registration.

## Docker Install

```bash
docker compose up --build
```
