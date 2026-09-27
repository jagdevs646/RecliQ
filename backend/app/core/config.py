from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Secrets come only from the environment (or a
    managed secret store that injects environment variables); nothing secret
    has a default here."""

    app_name: str = "RecliQ"
    environment: str = "development"
    api_prefix: str = "/api"
    frontend_origin: str = "http://localhost:5173"
    # Comma-separated list of extra allowed origins (e.g. Vercel preview URLs).
    extra_cors_origins: str = ""
    session_cookie_secure: bool = False

    database_url: str = "sqlite:///./reconx_dev.db"

    storage_backend: str = "local"
    local_storage_path: Path = Path("./storage")
    azure_storage_connection_string: str | None = None
    azure_storage_container: str = "reconx"

    # Upload limits. Files are checked by content (magic bytes), not by name.
    max_upload_mb: int = 50
    max_rows_per_sheet: int = 500_000

    # Job execution. "redis" runs jobs in durable RQ workers; "local" runs them
    # in an in-process thread pool (development and tests only).
    job_queue_backend: str = "local"
    redis_url: str = "redis://localhost:6379/0"
    job_queue_name: str = "recliq-reconciliation"
    job_timeout_seconds: int = 3600
    job_max_attempts: int = 3
    # A processing job whose heartbeat is older than this is treated as
    # orphaned (its worker died) and is retried or failed.
    job_lease_seconds: int = 180

    # Observability.
    log_level: str = "INFO"
    log_format: str = "json"
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.0
    release: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.local_storage_path.mkdir(parents=True, exist_ok=True)
    return settings
