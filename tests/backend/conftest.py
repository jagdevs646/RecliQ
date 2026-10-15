"""Test isolation: every run uses its own SQLite database and storage folder,
never the developer's local data. Settings are read once, so the environment
is set here, before any test module imports the app."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(ROOT))

_RUN_DIR = Path(tempfile.mkdtemp(prefix="recliq-tests-"))
os.environ.update({
    "ENVIRONMENT": "test",
    "DATABASE_URL": f"sqlite:///{(_RUN_DIR / 'test.db').as_posix()}",
    "STORAGE_BACKEND": "local",
    "LOCAL_STORAGE_PATH": str(_RUN_DIR / "storage"),
    "JOB_QUEUE_BACKEND": "local",
    "SESSION_COOKIE_SECURE": "false",
    "ENGINE": "rows",
    "MAX_UPLOAD_MB": "50",
    "MAX_ROWS_PER_SHEET": "500000",
    "LOG_FORMAT": "text",
    "LOG_LEVEL": "WARNING",
})
for _name in (
    "BOOTSTRAP_ADMIN_USERNAME",
    "BOOTSTRAP_ADMIN_EMAIL",
    "BOOTSTRAP_ADMIN_PASSWORD",
    "BOOTSTRAP_ADMIN_TOTP_SECRET",
):
    os.environ[_name] = ""

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    """Create the schema once, for tests that use the database directly."""
    from app.database.base import Base
    from app.database.session import engine

    Base.metadata.create_all(bind=engine)
    yield
