from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.local_storage_path.mkdir(parents=True, exist_ok=True)
    return settings
