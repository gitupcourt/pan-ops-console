"""Encrypted credentials (API keys or username/password) for firewalls and Panoramas."""

from datetime import datetime
from sqlalchemy import DateTime, Enum, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import AuthType, CredentialScope


class Credential(Base):
    """A reusable credential. Devices/Panoramas reference this by id.

    The actual secret material (api_key, or username+password JSON) is encrypted
    at rest via Fernet — see app.crypto. The DB only sees ciphertext.
    """

    __tablename__ = "credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    auth_type: Mapped[AuthType] = mapped_column(Enum(AuthType, name="auth_type"), nullable=False)
    scope: Mapped[CredentialScope] = mapped_column(
        Enum(CredentialScope, name="credential_scope"), nullable=False
    )

    # Ciphertext of either the API key, or a JSON blob {"username": "...", "password": "..."}.
    encrypted_secret: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
