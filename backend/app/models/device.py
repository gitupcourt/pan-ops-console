"""Firewall devices, with optional Panorama linkage.

Auth model: each device that's polled directly owns its own encrypted API key.
A device polled through Panorama (proxy_via_panorama=true) has no key of its
own and uses the parent Panorama's key with target-serial routing instead.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, LargeBinary, String, func
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

    # Per-device encrypted API key. Required when proxy_via_panorama=false.
    # Kept around even when proxying so flipping back doesn't lose the key.
    encrypted_api_key: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # When True, all metric ops route through the linked Panorama via
    # target-serial. The Panorama's key is used; this device's own key (if any)
    # is ignored.
    proxy_via_panorama: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    polling_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_poll_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
