"""PAN-OS image artifacts available for upgrades."""

from datetime import datetime
from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PanosImage(Base):
    """A PAN-OS image either uploaded to the app or referenced by version for device-side pull."""

    __tablename__ = "panos_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    # If filename is set, the image was uploaded to the app and lives on local disk
    # under settings.IMAGE_STORAGE_PATH. If null, this row simply records a version
    # that devices should pull themselves from updates.paloaltonetworks.com.
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
