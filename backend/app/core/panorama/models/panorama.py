"""Panorama instance — used as a device source and (optionally) as an API proxy.

Each Panorama owns its API key on its own row (encrypted at rest). PAN-OS keys
are scoped to a user on a specific host, so there's no value in a separate
reusable credentials store — see commit history for the design rationale.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, LargeBinary, String, false, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Panorama(Base):
    __tablename__ = "panoramas"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)

    # Fernet-encrypted API key. Created either by pasting a known key or by
    # running keygen() against this host with username+password once.
    encrypted_api_key: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    verify_tls: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )

    # Org-wide opt-OUT: when True (the default), upgrade orchestration may
    # proxy through this Panorama for its managed devices. When False, all
    # upgrade traffic for this Panorama's devices goes direct regardless of
    # the per-device `proxy_via_panorama` flag. Defaulting to True
    # (proxy-on) matches the operator-side directive captured in CLAUDE.md
    # ("default to Panorama proxied even for the upgrader function; the
    # operator has the option to disable") and the analogous behaviour on
    # the device side (Panorama-imported devices land with
    # `proxy_via_panorama=True`). The two-layer gate from MIGRATION_NOTES
    # §14 is preserved structurally — both flags must be True for proxy to
    # engage — but the default posture is proxy-on at both layers.
    proxy_upgrades: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    reachable: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    last_reachability_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reachability_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # The Panorama's OWN running PAN-OS version, captured from `show system
    # info` whenever we reach it (scheduled sync + Test connection). Surfaced on
    # the Inventory → Panoramas table. Nullable until the first successful reach.
    sw_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
