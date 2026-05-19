"""Storage interface for time-series samples.

The interface is the seam that makes the deferred work in [[ROADMAP]] possible
without painful refactors:

* swap SQLite for TimescaleDB → implement `SampleStore` for Timescale
* remote-collector mode → implement `SampleStore` as an HTTPS POST to the central API

The poller never imports SQLAlchemy or sqlite3 — it only sees the interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session

from app.models.sample import Sample


@dataclass
class SamplePoint:
    device_id: int
    metric: str
    ts: datetime
    current: float
    max: float | None
    pct: float | None


class SampleStore(Protocol):
    """Abstract storage. Implementations: SQLite (now), Timescale (later), HTTP (remote collector)."""

    def write_samples(self, samples: list[SamplePoint]) -> None: ...

    def read_range(
        self,
        device_id: int,
        metric: str,
        start: datetime,
        end: datetime,
    ) -> list[SamplePoint]: ...


class SQLAlchemySampleStore:
    """Default implementation. Writes through the same SQLAlchemy engine the
    rest of the app uses. Backed by SQLite by default; identical code works
    against Postgres/TimescaleDB if DATABASE_URL points there."""

    def __init__(self, session_factory):
        self._session_factory = session_factory

    def write_samples(self, samples: list[SamplePoint]) -> None:
        if not samples:
            return
        with self._session_factory() as db:  # type: Session
            db.add_all(
                Sample(
                    device_id=p.device_id,
                    metric=p.metric,
                    ts=p.ts,
                    current_value=p.current,
                    max_value=p.max,
                    pct=p.pct,
                )
                for p in samples
            )
            db.commit()

    def read_range(self, device_id, metric, start, end):
        with self._session_factory() as db:  # type: Session
            rows = (
                db.query(Sample)
                .filter(
                    Sample.device_id == device_id,
                    Sample.metric == metric,
                    Sample.ts >= start,
                    Sample.ts <= end,
                )
                .order_by(Sample.ts.asc())
                .all()
            )
            return [
                SamplePoint(
                    device_id=r.device_id,
                    metric=r.metric,
                    ts=r.ts,
                    current=r.current_value,
                    max=r.max_value,
                    pct=r.pct,
                )
                for r in rows
            ]
