"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    DATABASE_URL: str = "sqlite:///data/capacity.db"
    FERNET_KEY: str = Field(..., min_length=32)
    POLL_INTERVAL_SECONDS: int = 300
    CATALOG_PATH: str = "/app/catalog/metrics.yaml"
    CORS_ORIGINS: str = "http://localhost:5173"

    # Auth / sessions
    SESSION_COOKIE_NAME: str = "pcasession"
    SESSION_LIFETIME_SECONDS: int = 60 * 60 * 12  # 12 hours
    # Set true in prod (HTTPS); false for local docker-compose over plain HTTP.
    SESSION_COOKIE_SECURE: bool = True
    # SameSite policy. Lax is the right default for a SPA — top-level
    # navigations carry the cookie, but cross-site POSTs don't.
    SESSION_COOKIE_SAMESITE: str = "lax"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
