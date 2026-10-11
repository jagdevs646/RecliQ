from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.config import Settings


def test_local_email_transport_defaults_to_log_and_env_file_is_backend_scoped(monkeypatch):
    monkeypatch.delenv("EMAIL_BACKEND", raising=False)
    settings = Settings(_env_file=None, environment="development")

    assert settings.email_backend == "log"
    assert Path(Settings.model_config["env_file"]).resolve() == Path(__file__).resolve().parents[1] / ".env"


def test_relative_sqlite_url_resolves_under_backend_directory(monkeypatch):
    monkeypatch.chdir(Path(__file__).resolve().parents[2])
    settings = Settings(_env_file=None, database_url="sqlite:///./reconx_dev.db")

    actual_path = Path(make_url(settings.database_url).database).resolve()
    expected_path = Path(__file__).resolve().parents[1] / "reconx_dev.db"
    assert actual_path == expected_path
