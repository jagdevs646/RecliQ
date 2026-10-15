from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


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
    auth_cookie_name: str = "recliq_auth"
    auth_session_days: int = 30
    auth_csrf_secret: str | None = None
    auth_max_attempts: int = 8
    auth_attempt_window_minutes: int = 15
    registration_enabled: bool = True
    bootstrap_admin_username: str | None = None
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_totp_secret: str | None = None
    # Two-factor (TOTP) keys are stored encrypted with this key (AES-256-GCM).
    # A comma-separated list rotates keys: the first encrypts, all decrypt.
    # Local development and tests fall back to a fixed key.
    mfa_encryption_key: str | None = None

    # The log transport is local-only (enforced by email_service); production
    # still requires an explicitly configured SMTP transport.
    email_backend: str = "log"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_from_name: str = "RecliQ"
    smtp_starttls: bool = True

    google_client_id: str | None = None
    google_client_secret: str | None = None
    facebook_client_id: str | None = None
    facebook_client_secret: str | None = None
    facebook_graph_version: str = "v26.0"
    linkedin_client_id: str | None = None
    linkedin_client_secret: str | None = None
    public_app_url: str = "http://localhost:5173"
    public_api_url: str | None = None
    report_link_hours: int = 24

    database_url: str = "sqlite:///./reconx_dev.db"

    storage_backend: str = "local"
    local_storage_path: Path = Path("./storage")
    azure_storage_connection_string: str | None = None
    azure_storage_container: str = "reconx"

    # Upload limits. Files are checked by content (magic bytes), not by name.
    max_upload_mb: int = 50
    max_rows_per_sheet: int = 500_000

    # Guest workspaces are temporary: their uploads, runs, reports, saved
    # setups and learning are deleted this many hours after they were created.
    # Registered accounts keep their data.
    guest_retention_hours: int = 24

    # Job execution. "redis" runs jobs in durable RQ workers; "local" runs them
    # in an in-process thread pool (development and tests only).
    job_queue_backend: str = "local"
    # Keep the row engine as default until Phase 3 parity and performance gates pass.
    engine: str = "rows"
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

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_deployment_security(self) -> "Settings":
        self.engine = self.engine.strip().lower()
        if self.engine not in {"rows", "columns"}:
            raise ValueError("ENGINE must be either 'rows' or 'columns'.")
        if self.max_upload_mb < 1 or self.max_rows_per_sheet < 1:
            raise ValueError("Upload size and row limits must be positive integers.")
        if self.guest_retention_hours < 1:
            raise ValueError("GUEST_RETENTION_HOURS must be a positive integer.")
        if self.auth_max_attempts < 1 or self.auth_attempt_window_minutes < 1 or self.auth_session_days < 1:
            raise ValueError("Authentication limits and session duration must be positive integers.")
        if self.auth_csrf_secret and len(self.auth_csrf_secret.strip()) < 32:
            raise ValueError("AUTH_CSRF_SECRET must contain at least 32 non-whitespace characters.")
        if self.mfa_encryption_key and any(len(key.strip()) < 32 for key in self.mfa_encryption_key.split(",")):
            raise ValueError("Each MFA_ENCRYPTION_KEY must contain at least 32 non-whitespace characters.")
        if self.environment.lower() not in {"development", "test", "local"}:
            if not self.session_cookie_secure:
                raise ValueError("SESSION_COOKIE_SECURE must be true outside local development and tests.")
            if not self.auth_csrf_secret:
                raise ValueError("AUTH_CSRF_SECRET must be configured outside local development and tests.")
        try:
            database_url = make_url(self.database_url)
        except Exception:
            raise ValueError("DATABASE_URL is not a valid SQLAlchemy URL.") from None
        if database_url.drivername.startswith("sqlite") and database_url.database not in {None, "", ":memory:"}:
            database_path = Path(database_url.database)
            if not database_path.is_absolute():
                backend_dir = Path(__file__).resolve().parents[2]
                database_path = (backend_dir / database_path).resolve()
                self.database_url = database_url.set(database=str(database_path)).render_as_string(hide_password=False)
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def effective_auth_csrf_secret(self) -> str:
        if self.auth_csrf_secret:
            return self.auth_csrf_secret
        if self.environment.lower() in {"development", "test", "local"}:
            return "recliq-local-csrf-secret-change-before-deployment"
        return ""

    @property
    def mfa_encryption_keys(self) -> list[str]:
        """Keys for two-factor secrets, the encrypting one first. Locally the
        fixed development key is always included, so data encrypted before a
        key was configured can still be read (and is re-encrypted at start-up).
        Empty in production without MFA_ENCRYPTION_KEY."""
        keys = [key.strip() for key in (self.mfa_encryption_key or "").split(",") if key.strip()]
        if self.environment.lower() in {"development", "test", "local"}:
            keys.append("recliq-local-mfa-key-change-before-deployment")
        return keys


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.local_storage_path.mkdir(parents=True, exist_ok=True)
    return settings
