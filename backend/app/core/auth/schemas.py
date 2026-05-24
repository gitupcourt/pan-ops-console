"""Schemas for the auth surface: bootstrap, login, users, TOTP, OIDC providers."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.schema_utils import ensure_utc


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
        return ensure_utc(v)


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


# ---------- OIDC provider config (admin-managed) ----------

class OIDCProviderCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    display_name: str = Field(min_length=1, max_length=120)
    issuer: str = Field(min_length=8, max_length=500)
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: str = Field(min_length=1)
    scopes: str = "openid email profile"
    enabled: bool = True


class OIDCProviderUpdate(BaseModel):
    """Patch shape — every field optional. Pass `client_secret` only when
    rotating it; omit (or send null) to keep the existing value."""
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    issuer: str | None = Field(default=None, min_length=8, max_length=500)
    client_id: str | None = Field(default=None, min_length=1, max_length=255)
    client_secret: str | None = None
    scopes: str | None = None
    enabled: bool | None = None


class OIDCProviderRead(BaseModel):
    """What an admin sees. Note the client_secret is intentionally absent;
    the UI shows the field as write-only ("click to rotate").
    """
    id: int
    slug: str
    display_name: str
    issuer: str
    client_id: str
    scopes: str
    enabled: bool
    has_client_secret: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _utc_oidc(cls, v):
        return ensure_utc(v)
