"""Runtime-editable polling configuration (#89 PR-4).

A single row (id=1) holding the staggered dispatcher's tunables, so an
operator can adjust cadences + per-Panorama concurrency from Settings →
Polling **without a redeploy**. The dispatcher reads it every tick, so edits
take effect within one tick; the DB row wins over the `config.py` env
defaults. Seeded by migration 0012 from those defaults.

Only the dispatcher-honored knobs live here. The beat tick
(`POLL_DISPATCH_TICK_SECONDS`) stays an env/startup setting — Celery beat
reads its schedule once at boot, so changing how *often* the dispatcher runs
needs a restart, unlike the intervals/cap it *enforces* each run.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PollingConfig(Base):
    __tablename__ = "polling_config"

    id: Mapped[int] = mapped_column(primary_key=True)  # always 1 (singleton)
    system_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    config_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_concurrency_per_panorama: Mapped[int] = mapped_column(Integer, nullable=False)
    device_retry_backoff_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
