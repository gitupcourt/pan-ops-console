"""Firewall devices, with Panorama linkage. Schema mirrors pan-fw-upgrader's
Device for future merge — fields not needed by the capacity analyzer are kept
for forward-compatibility but stay optional/nullable.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import DeviceSource


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    serial: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sw_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    source: Mapped[DeviceSource] = mapped_column(
        Enum(DeviceSource, name="device_source"), default=DeviceSource.DIRECT, nullable=False
    )
    panorama_id: Mapped[int | None] = mapped_column(
        ForeignKey("panoramas.id"), nullable=True
    )
    panorama = relationship("Panorama", lazy="joined")

    credential_id: Mapped[int | None] = mapped_column(
        ForeignKey("credentials.id"), nullable=True
    )
    credential = relationship("Credential", lazy="joined")

    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # When True and the device is Panorama-managed, all metric ops route through
    # Panorama via target-serial. Required for devices we can't reach directly.
    proxy_via_panorama: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Whether the poller should currently sample this device.
    polling_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_poll_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
