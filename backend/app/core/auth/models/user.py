"""User accounts + sessions + TOTP backup codes.

Auth model is deliberately simple: username + Argon2-hashed password,
optional TOTP, DB-backed sessions. Roles are just is_admin for now;
RBAC granularity comes later if there's ever a real multi-user need.

TOTP secrets are encrypted at rest with FERNET_KEY, same as the firewall
API credentials — one sacred secret to lose, not two.

Backup codes are single-use static codes printed at TOTP enrollment.
We never store them in plaintext — only sha256 hashes — and consume
them by deleting the matching row on use.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    # Argon2id hash (passlib-style $argon2id$... string). Nullable because a
    # future OIDC-only user might have no local password.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Durable OIDC identity binding (AppSec F-1). OIDC's stable
    # identifier is (issuer, sub) — `sub` is unique only within an
    # issuer, so we scope it by the provider the claim came from.
    # Persisted at first successful link (bootstrap / invite match) and
    # matched FIRST on every subsequent callback, so identity no longer
    # rides on mutable email / UPN. Nullable: local-only users and
    # not-yet-linked invitees have neither.
    oidc_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    oidc_sub: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # TOTP — optional second factor, user-toggleable from their profile.
    # encrypted_totp_secret holds Fernet-encrypted base32 secret bytes.
    encrypted_totp_secret: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sessions: Mapped[list["Session"]] = relationship(
        "Session", back_populates="user", cascade="all, delete-orphan"
    )
    backup_codes: Mapped[list["BackupCode"]] = relationship(
        "BackupCode", back_populates="user", cascade="all, delete-orphan"
    )


class BackupCode(Base):
    """One single-use TOTP backup code. Stored as sha256 hex; the
    plaintext is shown to the user once at enrollment and never again.
    A consumed code is deleted, not flagged — there's no audit value in
    keeping them around.
    """

    __tablename__ = "backup_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user: Mapped[User] = relationship("User", back_populates="backup_codes", lazy="joined")
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Session(Base):
    """One row per logged-in browser. Token stored hashed (not the cookie
    value itself) so a DB read doesn't leak active sessions.
    """

    __tablename__ = "sessions"

    # SHA-256 of the random token that goes in the cookie. The cookie value
    # is the secret; the row only holds its hash. Indexed for O(1) lookup.
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user: Mapped[User] = relationship("User", back_populates="sessions", lazy="joined")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # User-Agent string, truncated. Used by the "active sessions" UI later.
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
