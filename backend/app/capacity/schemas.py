"""Capacity-module schemas: time-series samples and metric series envelopes."""

from datetime import datetime

from pydantic import BaseModel, field_validator

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
