"""Firewall devices, with HA-pair grouping and Panorama linkage."""

from datetime import datetime
from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import DeviceSource, HARole


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    # Management IP — preferred connection target. Avoids DNS dependency from
    # inside the app's container. Populated from Panorama or via direct probe.
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    serial: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)

    # Where this device came from. PANORAMA-sourced devices have panorama_id set.
    source: Mapped[DeviceSource] = mapped_column(
        Enum(DeviceSource, name="device_source"), default=DeviceSource.DIRECT, nullable=False
    )
    panorama_id: Mapped[int | None] = mapped_column(
        ForeignKey("panoramas.id"), nullable=True
    )
    panorama = relationship("Panorama", lazy="joined")

    # Used when the device is upgraded direct-to-device (not via Panorama).
    # For panorama-sourced devices, this can be null and the Panorama's credential is used.
    credential_id: Mapped[int | None] = mapped_column(
        ForeignKey("credentials.id"), nullable=True
    )
    credential = relationship("Credential", lazy="joined")

    # TLS cert verification when talking direct-to-device. Defaults on for prod;
    # set off only for self-signed lab firewalls.
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # When True and the device is panorama-managed, all probes / pre-checks are
    # routed through Panorama using its target-serial mechanism. Required for
    # devices we can't reach directly (cloud, behind NAT, segmented mgmt).
    proxy_via_panorama: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Snapshot of `request license info` — used by the precheck classifier to
    # apply rules like "Threat Prevention expiry is OK if Advanced Threat
    # Prevention is present". Refreshed on probe.
    licenses: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Pre-staging: a PAN-OS image we've explicitly downloaded to the device but
    # not yet installed. UI can show a "Staged: X.Y.Z" badge so operators can
    # pre-position images during business hours and run install during a
    # maintenance window. Set/cleared by stage service.
    staged_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    staged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    staged_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Snapshot of every PAN-OS image currently downloaded to the device's
    # software partition. Populated on probe. Includes the running version
    # too. Useful for: (a) showing "already downloaded" hints in the UI,
    # (b) the future disk-space self-help feature (cleanup of unused images).
    downloaded_versions: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # HA pairing. ha_peer_id points to the peer Device row; ha_role indicates active/passive.
    ha_peer_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"), nullable=True)
    ha_peer = relationship("Device", remote_side="Device.id", lazy="joined", post_update=True)
    ha_role: Mapped[HARole] = mapped_column(
        Enum(HARole, name="ha_role"), default=HARole.UNKNOWN, nullable=False
    )

    # Panorama-side metadata, useful as filters when picking targets
    device_group: Mapped[str | None] = mapped_column(String(255), nullable=True)
    template_stack: Mapped[str | None] = mapped_column(String(255), nullable=True)

    current_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Runtime / connection state pulled from Panorama (or direct probe later)
    connected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uptime: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Detailed HA state strings from Panorama (more granular than ha_role).
    # ha_role is the upgrade-orchestration view (active/passive/standalone);
    # ha_state is the raw string ("active", "passive", "tentative", "non-functional", ...).
    ha_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ha_sync_state: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Content versions
    app_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    threat_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    av_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wildfire_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    url_filtering_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gp_client_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # last_seen_at = last time we observed connected=True
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # last_refresh_at = last time we successfully fetched device state (regardless of connected)
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
