from __future__ import annotations

import os
import tempfile
from pathlib import Path


_TEST_ROOT = Path(tempfile.mkdtemp(prefix="recliq-v2-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TEST_ROOT / 'test.db').as_posix()}"
os.environ["LOCAL_STORAGE_PATH"] = str(_TEST_ROOT / "storage")
os.environ["ENVIRONMENT"] = "test"
os.environ["AUTH_CSRF_SECRET"] = "test-only-auth-csrf-secret-0123456789"
os.environ["EMAIL_BACKEND"] = "log"
for _key in (
    "BOOTSTRAP_ADMIN_USERNAME",
    "BOOTSTRAP_ADMIN_EMAIL",
    "BOOTSTRAP_ADMIN_PASSWORD",
    "BOOTSTRAP_ADMIN_TOTP_SECRET",
):
    os.environ[_key] = ""
os.environ["JOB_QUEUE_BACKEND"] = "sync"
os.environ["REGISTRATION_ENABLED"] = "true"
os.environ["MAX_UPLOAD_MB"] = "50"
os.environ["MAX_ROWS_PER_SHEET"] = "500000"

import pytest
from fastapi.testclient import TestClient

from app.database.base import Base
from app.database.session import engine
from app.main import app
from app.services.platform_service import initialize_platform_state

# Keep API integration tests quick while still exercising the real Argon2
# implementation; production uses pwdlib's recommended stronger parameters.
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from app.services import account_security

account_security.password_hasher = PasswordHash((Argon2Hasher(time_cost=1, memory_cost=1024, parallelism=1),))


@pytest.fixture(autouse=True)
def fresh_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    initialize_platform_state()
    yield


@pytest.fixture
def client(fresh_database):
    with TestClient(app) as test_client:
        yield test_client
