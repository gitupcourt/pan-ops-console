"""OIDC provider configuration.

Each row describes one configured identity provider (Authentik, Entra,
Keycloak, Okta, etc.). The client_secret is Fernet-encrypted at rest
using the same FERNET_KEY that protects firewall API credentials and
TOTP secrets — one sacred key, not three.

Admin-configurable through the UI; see app.services.oidc.load_providers
for the loader. Env-var-based providers continue to work as a
bootstrap-time fallback (documented in app.config) but the DB rows
take precedence on conflict.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, LargeBinary, String, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class OIDCProvider(Base):
    __tablename__ = "oidc_providers"

    id: Mapped[int] = mapped_column(primary_key=True)

    # URL-safe identifier. Appears in route paths like /auth/oidc/<slug>/login.
    # Validated lowercase + alnum + hyphens in the create endpoint.
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    # Rendered on the login page button (e.g. "Microsoft", "Authentik").
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)

    # IdP issuer URL (no trailing slash). Discovery doc is fetched at
    # <issuer>/.well-known/openid-configuration.
    issuer: Mapped[str] = mapped_column(String(500), nullable=False)

    # Public — fine in plaintext.
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Fernet-encrypted client secret. The plaintext only exists in the
    # decrypted form briefly during the token-exchange call.
    encrypted_client_secret: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    # Space-separated OIDC scopes. "openid email profile" by default;
    # individual IdPs may add their own (groups, etc.).
    scopes: Mapped[str] = mapped_column(String(500), nullable=False, default="openid email profile")

    # When disabled, the provider is hidden from /auth/bootstrap-status
    # and login/callback endpoints return 404. Lets you turn an IdP off
    # without deleting it (and losing its config).
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # When True, this provider's asserted identity (email / UPN /
    # preferred_username) is trusted for INITIAL account linking even when
    # the IdP does not send `email_verified` — e.g. Microsoft Entra, which
    # never emits that claim, so no Entra user can otherwise onboard.
    #
    # Default False preserves the F-1 hardening (verified-email-only
    # linking) for untrusted / multi-tenant IdPs, where a principal can
    # assert a victim's email/UPN → account takeover. Enable ONLY for an
    # IdP you control (your own tenant). Still strictly invite-only: a
    # matching pre-existing local account must exist; this relaxes the
    # email_verified requirement, NOT the "must be pre-invited" rule.
    trusted_identity: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=false()
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
