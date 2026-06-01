"""Capacity-module schemas: time-series samples and metric series envelopes."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.schema_utils import ensure_utc


class SampleRead(BaseModel):
    # Field names match the API surface (current / max / pct) NOT the
    # SQLAlchemy column names (current_value / max_value). Aliases were
    # tried but FastAPI defaults to `response_model_by_alias=True`, so
    # JSON output ended up as `current_value` while the frontend
    # expected `current` — silent type mismatch, empty charts. Construct
    # SampleRead from the storage layer's SamplePoint dataclass (which
    # also uses these names) and there's no impedance mismatch.
    ts: datetime
    current: float
    max: float | None = None
    pct: float | None

    model_config = {"from_attributes": True}

    @field_validator("ts", mode="before")
    @classmethod
    def _ts_to_utc(cls, v):
        return ensure_utc(v)


class MetricSeries(BaseModel):
    device_id: int
    metric: str
    samples: list[SampleRead]


# ---- Runtime polling config (#89 PR-4) ----

class PollingConfigRead(BaseModel):
    """Operator-visible polling tunables. The four mutable fields are read by
    the dispatcher each tick; `dispatch_tick_seconds` is info-only (a beat
    startup setting, surfaced so the UI can explain the cadence)."""
    system_interval_seconds: int
    config_interval_seconds: int
    max_concurrency_per_panorama: int
    device_retry_backoff_seconds: int
    dispatch_tick_seconds: int  # info-only (env/startup; not DB-stored)
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("updated_at", mode="before")
    @classmethod
    def _utc(cls, v):
        return ensure_utc(v)


class PollingConfigUpdate(BaseModel):
    """PATCH shape — every field optional; bounds keep an operator from a
    footgun (a 0s interval busy-loops; a huge per-Panorama cap hammers the
    Panorama API)."""
    system_interval_seconds: int | None = Field(default=None, ge=30, le=86_400)
    config_interval_seconds: int | None = Field(default=None, ge=300, le=604_800)
    max_concurrency_per_panorama: int | None = Field(default=None, ge=1, le=50)
    device_retry_backoff_seconds: int | None = Field(default=None, ge=0, le=3_600)


class PanoramaPressure(BaseModel):
    pano_key: str          # Panorama id (string) or "direct"
    label: str             # Panorama name, or "Direct-polled"
    in_flight: int | None  # current concurrent polls (None if telemetry unavailable)
    cap: int               # the per-Panorama concurrency cap in effect
    devices: int           # enabled devices in this group
    devices_in_error: int  # enabled devices whose last poll errored


class PollingPressure(BaseModel):
    per_panorama: list[PanoramaPressure]
