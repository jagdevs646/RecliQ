# RecliQ Backend

FastAPI backend for the anonymous RecliQ MVP. It has no authentication: each visitor receives a UUID v4 session in an HttpOnly cookie and `X-Session-ID` response header. Files, jobs, history, and reports are filtered by that session.

Set `SESSION_COOKIE_SECURE=true` when serving the application over HTTPS. The local default is `false` so the cookie works on `http://localhost`.

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
