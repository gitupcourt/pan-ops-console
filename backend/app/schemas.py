"""Pydantic schemas for the API surface.

Auth model: API keys live on the Device / Panorama row. Creating or editing
one of those rows takes an optional `auth` payload that says either "mint a
key now from username+password" or "here's an API key, store it." If `auth`
is omitted on edit, the existing stored key is kept.
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.enums import DeviceSource


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """SQLite drops the timezone on datetimes even when the column is
    declared `DateTime(timezone=True)`. Everything in the DB is *written*
    as UTC, so when we read it back naive we reattach UTC. Without this
    the JSON serializer emits ISO strings with no offset, and the
    browser's `new Date(...)` interprets them as LOCAL time — making
    every chart appear empty because the points land in the future
    relative to `now()`.
    """
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


# ---------- Auth payloads (used inline in Device/Panorama create/update) ----------

class AuthFromUserpass(BaseModel):
    mode: Literal["userpass"] = "userpass"
    username: str
    password: str


class AuthFromApiKey(BaseModel):
    mode: Literal["api_key"] = "api_key"
    api_key: str


# ---------- Panoramas ----------

class PanoramaCreate(BaseModel):
    name: str
    hostname: str
    verify_tls: bool = True
    # Required at create time. Optional on PATCH (omit to keep the existing key).
    auth: AuthFromUserpass | AuthFromApiKey | None = Field(default=None, discriminator="mode")


class PanoramaRead(BaseModel):
    id: int
    name: str
    hostname: str
    has_api_key: bool
    verify_tls: bool
    reachable: bool
    last_sync_at: datetime | None
    last_reachability_at: datetime | None
    last_reachability_error: str | None

    model_config = {"from_attributes": True}

    @field_validator("last_sync_at", "last_reachability_at", mode="before")
    @classmethod
    def _utc(cls, v):
        return _ensure_utc(v)


# ---------- Devices ----------

class DeviceCreate(BaseModel):
    name: str
    hostname: str
    ip_address: str | None = None
    panorama_id: int | None = None
    verify_tls: bool = True
    proxy_via_panorama: bool = False
    polling_enabled: bool = True
    # Required on create when proxy_via_panorama=false. Optional on PATCH.
    auth: AuthFromUserpass | AuthFromApiKey | None = Field(default=None, discriminator="mode")


class DeviceRead(BaseModel):
    id: int
    name: str
    hostname: str
    ip_address: str | None
    serial: str | None
    model: str | None
    sw_version: str | None
    source: DeviceSource
    panorama_id: int | None
    has_api_key: bool
    verify_tls: bool
    proxy_via_panorama: bool
    polling_enabled: bool
    last_poll_at: datetime | None
    last_poll_error: str | None

    model_config = {"from_attributes": True}

    @field_validator("last_poll_at", mode="before")
    @classmethod
    def _utc(cls, v):
        return _ensure_utc(v)


# ---------- Samples ----------

class SampleRead(BaseModel):
    # Field names match the API surface (current / max / pct) NOT the
    # SQLAlchemy column names (current_value / max_value). Aliases were
    # tried but FastAPI defaults to `response_model_by_alias=True`, so
    # JSON output ended up as `current_value` while the frontend
    # expected `current` — silent type mismatch, empty charts. Construct
    # SampleRead from the storage layer's SamplePoint dataclass (which
    # also uses these names) and there's no impedance mismatch.
    ts: datetime
    current: float
    max: float | None = None
    pct: float | None

    model_config = {"from_attributes": True}

    @field_validator("ts", mode="before")
    @classmethod
    def _ts_to_utc(cls, v):
        return _ensure_utc(v)


class MetricSeries(BaseModel):
    device_id: int
    metric: str
    samples: list[SampleRead]


# ---------- Panorama device import ----------

class PanoramaDevicePreview(BaseModel):
    """One row from `show devices all` on Panorama, with a flag for whether
    we already have it in our DB. The UI uses these to populate the import
    picker.
    """
    serial: str
    hostname: str | None
    ip_address: str | None
    model: str | None
    sw_version: str | None
    connected: bool
    already_imported: bool


class PanoramaSyncRequest(BaseModel):
    """Filter sync to a specific set of serials. Empty list / missing = import all."""
    serials: list[str] | None = None


# =====================================================================
# Auth
# =====================================================================

class BootstrapStatus(BaseModel):
    """Probed at app load to decide what login page to show.

    needs_bootstrap=True means no users exist yet; the UI should render
    the first-user setup screen. oidc_providers lists configured SSO
    options (empty until Phase 2).
    """
    needs_bootstrap: bool
    oidc_providers: list[str] = []


class LoginRequest(BaseModel):
    username: str
    password: str
    # Phase 1.5 — TOTP code if the user has 2FA enabled
    totp_code: str | None = None


class SignupFirstRequest(BaseModel):
    """Only valid when needs_bootstrap is True. The created user is auto-admin."""
    username: str = Field(min_length=3, max_length=64)
    email: str | None = None
    password: str


class UserRead(BaseModel):
    id: int
    username: str
    email: str | None
    is_admin: bool
    is_active: bool
    totp_enabled: bool
    created_at: datetime
    last_login_at: datetime | None

    model_config = {"from_attributes": True}

    @field_validator("created_at", "last_login_at", mode="before")
    @classmethod
    def _utc(cls, v):
        return _ensure_utc(v)


class UserCreate(BaseModel):
    """Admin-only: invite a new user. Caller picks a temporary password."""
    username: str = Field(min_length=3, max_length=64)
    email: str | None = None
    password: str
    is_admin: bool = False


class PasswordChangeRequest(BaseModel):
    """Authenticated user changing their own password."""
    current_password: str
    new_password: str


# ---------- TOTP ----------

class TOTPSetupResponse(BaseModel):
    """Returned from /auth/totp/setup. The secret is encrypted at rest
    immediately, but the plaintext goes back to the user this one time
    so they can paste it into an authenticator app manually if they
    can't scan the QR. After /auth/totp/verify succeeds, this secret is
    "locked in" and can only be retrieved by disabling TOTP and starting
    fresh.
    """
    secret: str
    otpauth_uri: str


class TOTPVerifyRequest(BaseModel):
    code: str


class TOTPVerifyResponse(BaseModel):
    """Backup codes shown ONCE on successful enrollment. Cannot be
    retrieved again — losing them means disabling and re-enrolling TOTP.
    """
    backup_codes: list[str]


class TOTPDisableRequest(BaseModel):
    """Disabling TOTP requires the password as a second factor of sorts
    — we don't want a hijacked session (e.g. a left-open laptop) to be
    able to silently strip second-factor protection."""
    password: str


class LoginNeedsTOTPResponse(BaseModel):
    """Returned (with status 200) when password is valid but TOTP is
    required. Frontend collects the code and retries the login call
    with `totp_code` populated.
    """
    needs_totp: bool = True
