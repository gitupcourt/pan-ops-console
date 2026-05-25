"""Panorama instance — used as a target source and (optionally) as an upgrade proxy."""

from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Panorama(Base):
    __tablename__ = "panoramas"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)

    credential_id: Mapped[int] = mapped_column(ForeignKey("credentials.id"), nullable=False)
    credential = relationship("Credential", lazy="joined")

    # When True, upgrades for devices managed by this Panorama will be proxied through it
    # instead of going direct-to-device.
    proxy_upgrades: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # When True, validate the TLS cert chain. For a Panorama with a real cert (LetsEncrypt
    # or internal CA bundle on the host), keep this on. For self-signed labs, set to False.
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Reachability — updated on every attempt to talk to this Panorama
    # (sync_panorama, probe via proxy, /test-connection).  The UI uses these
    # to show a clear "Panorama is unreachable" banner and to offer a direct
    # fallback for individual device probes.
    reachable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_reachability_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reachability_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
