"""Panorama instance — used as a device source and (optionally) as an API proxy."""

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

    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    reachable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_reachability_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reachability_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
