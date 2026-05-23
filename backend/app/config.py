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

    # Public URL where this app is reachable from a browser. Used to
    # construct the OIDC redirect_uri the IdP calls back to. If left empty,
    # the OIDC routes fall back to deriving from the request — fine for
    # local dev, but most IdPs reject mismatched redirect URIs, so set
    # this explicitly in production.
    PUBLIC_BASE_URL: str = ""

    # OIDC providers are read from env vars matching:
    #   OIDC_PROVIDER_<NAME>_ISSUER          = "https://idp.example.com"
    #   OIDC_PROVIDER_<NAME>_CLIENT_ID       = "..."
    #   OIDC_PROVIDER_<NAME>_CLIENT_SECRET   = "..."
    #   OIDC_PROVIDER_<NAME>_DISPLAY_NAME    = "Authentik"        (optional)
    #   OIDC_PROVIDER_<NAME>_SCOPES          = "openid email profile" (optional)
    # The lookup happens at startup in app.services.oidc.load_providers().
    # `extra="ignore"` (set above) lets these pass through without being
    # named here on the Settings class.

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
