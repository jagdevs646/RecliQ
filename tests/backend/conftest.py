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
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(_RUN_DIR / 'test.db').as_posix()}")
os.environ.setdefault("LOCAL_STORAGE_PATH", str(_RUN_DIR / "storage"))
os.environ.setdefault("JOB_QUEUE_BACKEND", "local")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    """Create the schema once, for tests that use the database directly."""
    from app.database.base import Base
    from app.database.session import engine

    Base.metadata.create_all(bind=engine)
    yield
