"""Time-series sample — one row per (device, metric, timestamp).

Schema is deliberately narrow: device_id + metric name + ts + current + max + pct.
Keeping it flat means we can swap SQLite for TimescaleDB without contortions
(see [[ROADMAP]]).
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Sample(Base):
    __tablename__ = "samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    metric: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    # Both stored. `current` and `max` because some metrics are inherently
    # unitless counts; storing the pair lets us recompute % later if the max
    # changes (e.g. after a PAN-OS upgrade that lifts a platform limit).
    current_value: Mapped[float] = mapped_column(Float, nullable=False)
    max_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("ix_samples_device_metric_ts", "device_id", "metric", "ts"),
    )
