# Local Development

## Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

For quick local work without PostgreSQL, set:

```env
DATABASE_URL=sqlite:///./recliq_dev.db
STORAGE_BACKEND=local
LOCAL_STORAGE_PATH=./storage
FRONTEND_ORIGIN=http://127.0.0.1:5173
```

## Frontend

```bash
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm run dev
```

Set `VITE_API_BASE_URL=http://localhost:8000/api` if your backend uses a different origin.

The frontend opens directly to the Dashboard; no account setup is required.

## Tests

```bash
cd backend
pytest ../tests/backend
```
