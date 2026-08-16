#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# start.sh  –  Render boot script for the RecliQ FastAPI backend
#
# Responsibilities:
#   1. Convert Render's $RENDER_DB_URL (postgresql://...) to the psycopg3
#      scheme (postgresql+psycopg://...) and export it as DATABASE_URL.
#   2. Run Alembic migrations (safe to run on every boot; idempotent).
#   3. Start Uvicorn.
# ---------------------------------------------------------------------------
set -euo pipefail

# ── 1. Build DATABASE_URL from Render's injected connection string ──────────
if [[ -n "${RENDER_DB_URL:-}" ]]; then
  # Render injects  postgresql://user:pass@host:5432/db
  # psycopg3 needs  postgresql+psycopg://user:pass@host:5432/db
  export DATABASE_URL="${RENDER_DB_URL/postgresql:\/\//postgresql+psycopg:\/\/}"
  echo "[start.sh] DATABASE_URL set from RENDER_DB_URL (psycopg3 scheme)"
elif [[ -z "${DATABASE_URL:-}" ]]; then
  echo "[start.sh] ERROR: Neither RENDER_DB_URL nor DATABASE_URL is set." >&2
  exit 1
fi

# ── 2. Run Alembic migrations ───────────────────────────────────────────────
echo "[start.sh] Running Alembic migrations…"
alembic upgrade head
echo "[start.sh] Migrations complete."

# ── 3. Start the application ────────────────────────────────────────────────
echo "[start.sh] Starting Uvicorn…"
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 2 \
  --proxy-headers \
  --forwarded-allow-ips "*"
