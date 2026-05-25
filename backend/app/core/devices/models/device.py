"""Firewall devices — unified inventory for capacity polling + upgrade orchestration.

Auth model: each device that's polled directly owns its own encrypted API key.
A device polled through Panorama (proxy_via_panorama=true) has no key of its
own and uses the parent Panorama's key with target-serial routing instead.

Schema reconciliation (phase 4a): added the upgrader's columns (HA fields,
content-version tracking, staging metadata, Panorama-side filters) so a
single `devices` table feeds both the capacity poller and upgrade jobs.
See CLAUDE.md "Phase 4a design contract" for the binding decisions.

The transitional `sw_version` column duplicates `current_version` (both
hold the running PAN-OS version). Phase 4b will switch capacity's
collector to write `current_version` and drop `sw_version`.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    LargeBinary,
    String,
    false,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.devices.models.enums import DeviceSource, HARole
from app.db import Base


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    serial: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Running PAN-OS version. `sw_version` is the original capacity column;
    # `current_version` is the upgrader-side name. Phase 4b: writes go to
    # both, reads prefer `current_version`, then `sw_version` falls away.
    sw_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    source: Mapped[DeviceSource] = mapped_column(
        Enum(DeviceSource, name="device_source"),
        default=DeviceSource.DIRECT,
        server_default="direct",
        nullable=False,
    )
    panorama_id: Mapped[int | None] = mapped_column(
        ForeignKey("panoramas.id"), nullable=True
    )
    panorama = relationship("Panorama", lazy="joined")

    # Per-device encrypted API key. Required when proxy_via_panorama=false.
    # Kept around even when proxying so flipping back doesn't lose the key.
    # The credentials-table-vs-inline question is deferred (phase 4c) — see
    # CLAUDE.md. Current state: inline (capacity pattern preserved).
    encrypted_api_key: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    verify_tls: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    # When True, all metric ops route through the linked Panorama via
    # target-serial. The Panorama's key is used; this device's own key (if any)
    # is ignored.
    proxy_via_panorama: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )

    polling_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )

    # ----- HA pairing (carry-forward from upgrader) -----
    # ha_role: upgrade-orchestrator's view (standalone/active/passive).
    # ha_state / ha_sync_state: raw Panorama HA daemon strings — more granular
    # than ha_role and the load-bearing signal in the upgrader's wait-for-passive
    # logic. Per MIGRATION_NOTES §5, both are required for the orchestrator's
    # HA safety belts to work; preserve exactly when phase 4c arrives.
    ha_peer_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"), nullable=True)
    ha_peer = relationship(
        "Device", remote_side="Device.id", lazy="joined", post_update=True
    )
    ha_role: Mapped[HARole] = mapped_column(
        Enum(HARole, name="ha_role"),
        default=HARole.UNKNOWN,
        server_default="unknown",
        nullable=False,
    )
    ha_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ha_sync_state: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ----- Panorama-side metadata (used as job-target filters) -----
    device_group: Mapped[str | None] = mapped_column(String(255), nullable=True)
    template_stack: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ----- Runtime / connection state -----
    # `connected` is set by Panorama sync's response (authoritative for
    # "device is currently reachable per Panorama"). `last_seen_at` is the
    # last time we successfully *talked* to the device directly. Two
    # different signals, both meaningful — don't conflate.
    connected: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    uptime: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ----- Staging / image management -----
    # Pre-staging: a PAN-OS image we've explicitly downloaded to the device
    # but not yet installed. UI surfaces a "Staged: X.Y.Z" badge.
    staged_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    staged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    staged_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Every PAN-OS image currently downloaded on the device. JSON list.
    downloaded_versions: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # ----- License + content snapshots -----
    # JSON, not separate rows. Per MIGRATION_NOTES §14: licenses change
    # frequently and we never query into them at the SQL level, only render
    # whole — a sub-table would be expensive overkill.
    licenses: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    threat_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    av_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wildfire_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    url_filtering_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gp_client_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Capacity-side timestamps
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_poll_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Upgrader-side timestamps
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
