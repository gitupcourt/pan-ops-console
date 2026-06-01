"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import Field, model_validator
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

    # --- Capacity polling cadences (#89) -------------------------------
    # The catalog has two classes of metric with very different volatility:
    #   * system/traffic — live telemetry (CPU, memory, sessions,
    #     throughput). Changes continuously; we want it fresh.
    #   * config — object/policy counts (address objects, security rules,
    #     NAT, VPN peers, …). Only moves when an operator commits config,
    #     so re-reading it every few minutes burns Panorama API calls for
    #     near-identical data.
    # Polling them on separate beats lets one Panorama proxy far more
    # devices within its API envelope: the bulk of the catalog (the
    # config-class metrics) drops from every-few-minutes to hourly, while
    # live telemetry stays fast. Scaling polling to live inside a single
    # Panorama's budget is the explicit goal — NOT "add another Panorama."
    #
    # POLL_INTERVAL_SECONDS is retained as the back-compat knob for the
    # FAST (system/traffic) cadence: existing installs that tuned it keep
    # their live-telemetry interval unchanged. POLL_SYSTEM_INTERVAL_SECONDS
    # overrides it when set explicitly; left unset it inherits this value
    # (resolved in _resolve_poll_intervals below).
    POLL_INTERVAL_SECONDS: int = 300
    # Explicit override for the fast (system/traffic) cadence. None →
    # inherit POLL_INTERVAL_SECONDS so legacy single-knob configs Just Work.
    POLL_SYSTEM_INTERVAL_SECONDS: int | None = None
    # Slow cadence for config-class metrics. Default 12h (twice a day) —
    # object/policy counts change on commit, not on a timer, so re-reading
    # them constantly burns Panorama API budget for near-identical data.
    # The alert engine cares about live telemetry (fast beat), not object
    # counts. Operator-configurable at runtime in PR-4.
    POLL_CONFIG_INTERVAL_SECONDS: int = 43200

    # --- Staggered dispatcher (#89 PR-3) -------------------------------
    # Polling is driven by a per-device "due-time" dispatcher rather than a
    # single sweep: a lightweight beat fires `capacity.dispatch_due` every
    # POLL_DISPATCH_TICK_SECONDS, which enqueues per-device poll tasks for
    # only the devices whose class interval has elapsed, bounded per
    # Panorama. This spreads the fleet across each interval instead of one
    # thundering herd, and bounds each Panorama's API load independently.
    POLL_DISPATCH_TICK_SECONDS: int = 30
    # Max concurrent in-flight polls through any ONE Panorama (proxied) or
    # the "direct" group. Protects a single Panorama's API; aggregate fleet
    # throughput scales with the NUMBER of Panoramas. Total poll parallelism
    # is bounded by the dedicated poller worker's --concurrency.
    POLL_MAX_CONCURRENCY_PER_PANORAMA: int = 4
    # On a failed device poll, rewind the class timestamp so it retries in
    # ~this many seconds instead of waiting a full (up to 12h) interval.
    POLL_DEVICE_RETRY_BACKOFF_SECONDS: int = 60

    # How often the scheduled Panorama sync runs (refresh `Device.connected`,
    # `last_seen_at`, HA state, etc. across every registered Panorama).
    # Default 5 minutes — same cadence as the capacity poller, low cost
    # since Panorama's `show devices all` is one call per Panorama
    # regardless of fleet size. Set higher if Panorama is rate-limit-
    # sensitive or the operator wants slower refresh; the UI's
    # disconnected/online pill freshness scales with this.
    PANORAMA_SYNC_INTERVAL_SECONDS: int = 300

    # Per-call timeout (seconds) for the PAN XML-API client. Bounds how long a
    # single op() waits on the socket — connect AND read. Without it, pan-os-
    # python leaves the timeout unset and a DIRECT device that's unreachable
    # rides the OS TCP-connect default (~127s) per command; with 3 distinct
    # capacity commands that's ~6.75 min of a poller slot pinned on one dead
    # device (observed on a down PA-220). 30s fails a dead device fast while
    # staying well clear of any healthy op() response time (status queries
    # return in <10s even on busy devices; long upgrade waits use their own
    # separate timeouts, not op()). Tune down to fail dead devices faster, or
    # up if a slow WAN to a branch device trips false timeouts.
    PAN_CLIENT_TIMEOUT_SECONDS: int = 30

    CATALOG_PATH: str = "/app/catalog/metrics.yaml"
    CORS_ORIGINS: str = "http://localhost:5173"

    # Redis URL — used as Celery broker + result backend. Format:
    # redis://[:password@]host:port/db. Default points at the in-cluster
    # Redis Service name expected to exist post-phase-2d; local dev /
    # tests don't need Redis (capacity polling stays on APScheduler
    # until phase 2e wires the Celery beat schedule).
    REDIS_URL: str = "redis://redis:6379/0"

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

    @model_validator(mode="after")
    def _resolve_poll_intervals(self) -> "Settings":
        """Let POLL_SYSTEM_INTERVAL_SECONDS fall back to the legacy
        POLL_INTERVAL_SECONDS knob when not set explicitly.

        Keeps single-knob installs behaving exactly as before: whatever
        they set POLL_INTERVAL_SECONDS to becomes the live-telemetry
        cadence. Setting POLL_SYSTEM_INTERVAL_SECONDS explicitly wins.
        """
        if self.POLL_SYSTEM_INTERVAL_SECONDS is None:
            self.POLL_SYSTEM_INTERVAL_SECONDS = self.POLL_INTERVAL_SECONDS
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
