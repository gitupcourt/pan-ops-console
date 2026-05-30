"""Aggregation endpoints for the Capacity Analyzer UI (phases 9-11).

Three views drill into capacity data from broad-fleet to single-metric:

  - GET /capacity/heatmap          → per (model, metric): max%, device_count,
                                     top-N devices. Powers the phase-9 grid.
  - GET /capacity/table            → flat per (device, metric) with filters.
                                     Powers the phase-10 table.
  - GET /capacity/trend/{d}/{m}    → time-series for one device-metric +
                                     optional linear-regression prediction.
                                     Powers the phase-11 chart.

These all read from the existing `samples` table (the capacity poller's
write target) joined against `devices` for model/group/template metadata.
No new schema — all the data we need is already there.

The expensive query in all of these is "latest sample per (device,
metric)". On Postgres this is one `DISTINCT ON (device_id, metric) ...
ORDER BY device_id, metric, ts DESC` query and runs sub-second against
the existing composite index `ix_samples_device_metric_ts`. On SQLite
(tests) DISTINCT ON isn't supported, so we fall back to a window
function via `row_number() OVER (PARTITION BY device_id, metric ORDER
BY ts DESC) = 1`, which works on both engines.

Pagination / scale: today we return all rows. Phase 15 introduces
`sample_rollups_*` tables that pre-aggregate the latest values, at
which point these endpoints become single-row reads of the rollup.
The endpoint shapes are designed so phase 15 is a backend swap with
no frontend change.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.capacity.models.sample import Sample
from app.capacity.services.catalog import MetricSpec, load_catalog
from app.core.devices.models.device import Device
from app.db import get_db

router = APIRouter(prefix="/capacity", tags=["capacity"])


# ---------- response schemas ----------


class HeatmapDeviceTop(BaseModel):
    device_id: int
    device_name: str
    current: float
    max: float | None
    pct: float | None


class HeatmapCell(BaseModel):
    """One (model, metric) tile on the heat-map grid.

    `max_pct` is the highest % across all devices of this model for this
    metric — the value that drives the tile's color. `device_count` is
    the cardinality for the operator's hover popover ("Devices: 22").
    `top_devices` is the leaderboard surfaced in that popover.
    """

    model: str
    metric: str
    category: str  # "config" / "system" / "traffic"
    metric_description: str
    max_pct: float | None
    device_count: int
    top_devices: list[HeatmapDeviceTop]


class TableRow(BaseModel):
    """One (device, metric) row on the table view."""

    device_id: int
    device_name: str
    # serial + ip surfaced so the table search can match a device by
    # the same identifiers as Inventory (name / IP / serial), not just
    # display name. Null on devices that haven't reported them yet.
    serial: str | None
    ip_address: str | None
    model: str | None
    software_version: str | None
    device_group: str | None
    template_stack: str | None
    metric: str
    metric_description: str
    category: str
    current: float
    max: float | None
    pct: float | None
    # Predicted date is intentionally NULL on this endpoint — computing
    # linear regression per row across N devices × 17 metrics on every
    # table fetch is expensive. Phase 15 will pre-compute nightly into
    # a rollup table; phase 11's /capacity/trend endpoint computes one
    # at a time on demand for the chart view.
    predicted_date: datetime | None
    last_sample_at: datetime


class TableResponse(BaseModel):
    rows: list[TableRow]
    total: int


class TrendSample(BaseModel):
    ts: datetime
    current: float
    pct: float | None


class TrendPrediction(BaseModel):
    """Linear-regression projection of when the metric hits its max.

    Cheap to compute per request (~30 samples, one numpy-style fit).
    See the predicted-date math in `_predict_max_date` for the rules
    around insufficient data / decreasing trends.
    """

    predicted_date: datetime | None
    days_left: int | None
    slope_per_day: float | None
    method: str  # "linear_regression" / "insufficient_data" / "decreasing"


class TrendResponse(BaseModel):
    device_id: int
    device_name: str
    model: str | None
    metric: str
    metric_description: str
    max_value: float | None
    samples: list[TrendSample]
    prediction: TrendPrediction


# ---------- helpers ----------


def _latest_samples_query(db: Session):
    """SELECT for "the most recent sample per (device_id, metric)".

    Uses ROW_NUMBER for cross-dialect compatibility (DISTINCT ON is
    Postgres-only). On Postgres the planner picks an efficient
    index-only scan against ix_samples_device_metric_ts; on SQLite
    it's a sort + filter that's fine for the test sizes.

    Returns a SELECT that includes one row per (device_id, metric) —
    callers add their own filters / joins.
    """
    rn = func.row_number().over(
        partition_by=(Sample.device_id, Sample.metric),
        order_by=Sample.ts.desc(),
    ).label("rn")
    inner = select(Sample, rn).subquery()
    SampleAlias = aliased(Sample, inner)
    return select(SampleAlias).where(inner.c.rn == 1)


def _catalog_lookup() -> dict[str, MetricSpec]:
    """Catalog keyed by metric name for fast description/category lookup."""
    return {m.name: m for m in load_catalog()}


def _predict_max_date(
    samples: list[tuple[datetime, float]],
    max_value: float | None,
    *,
    min_samples: int = 5,
) -> TrendPrediction:
    """Linear regression on (ts, current_value) → predicted max-hit date.

    Returns a "no projection" result if:
      - max_value isn't known (some metrics are unitless counts)
      - fewer than `min_samples` points (need real trend signal)
      - slope is ≤ 0 (stable or shrinking — won't hit max)
      - current already ≥ max (already there)

    Otherwise: days_until = (max - current) / slope, projected from now.
    """
    if max_value is None or max_value <= 0:
        return TrendPrediction(
            predicted_date=None, days_left=None,
            slope_per_day=None, method="insufficient_data",
        )
    if len(samples) < min_samples:
        return TrendPrediction(
            predicted_date=None, days_left=None,
            slope_per_day=None, method="insufficient_data",
        )

    # Reduce ts to days-since-first for numerical stability and a
    # human-meaningful slope unit.
    t0 = samples[0][0]
    xs = [(ts - t0).total_seconds() / 86400.0 for ts, _ in samples]
    ys = [v for _, v in samples]
    n = len(samples)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return TrendPrediction(
            predicted_date=None, days_left=None,
            slope_per_day=None, method="insufficient_data",
        )
    slope = num / den
    if slope <= 0:
        return TrendPrediction(
            predicted_date=None, days_left=None,
            slope_per_day=slope, method="decreasing",
        )

    current = ys[-1]
    if current >= max_value:
        return TrendPrediction(
            predicted_date=samples[-1][0],
            days_left=0,
            slope_per_day=slope,
            method="linear_regression",
        )
    days_left = (max_value - current) / slope
    predicted = samples[-1][0] + timedelta(days=days_left)
    return TrendPrediction(
        predicted_date=predicted,
        days_left=int(round(days_left)),
        slope_per_day=slope,
        method="linear_regression",
    )


# ---------- /capacity/heatmap ----------


@router.get("/heatmap", response_model=list[HeatmapCell])
def get_heatmap(
    device_group: str | None = Query(default=None),
    template_stack: str | None = Query(default=None),
    top_n: int = Query(default=8, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Tile grid for the heat-map view.

    Returns one row per (device_model, metric) combination present in
    the data. Filtering happens at the device level before aggregation,
    so a DG/TS filter narrows BOTH which devices contribute to max_pct
    AND which appear in top_devices.

    `top_n` controls how many devices the hover popover gets. Default 8
    fits comfortably in the popover while leaving room for a "show N
    more" affordance when the bucket has more than top_n devices.
    """
    # Step 1: latest sample per (device_id, metric), joined with the
    # device row so we have model + filter columns in scope.
    latest = _latest_samples_query(db).subquery()
    q = (
        select(
            Device.id.label("device_id"),
            Device.name.label("device_name"),
            Device.model,
            Device.device_group,
            Device.template_stack,
            latest.c.metric,
            latest.c.current_value,
            latest.c.max_value,
            latest.c.pct,
        )
        .join(Device, Device.id == latest.c.device_id)
    )
    if device_group is not None:
        q = q.where(Device.device_group == device_group)
    if template_stack is not None:
        q = q.where(Device.template_stack == template_stack)

    rows = db.execute(q).all()
    if not rows:
        return []

    # Step 2: group in Python by (model, metric). For each group:
    #   - max_pct = max of all pct (None-safe)
    #   - device_count = unique device count
    #   - top_devices = top N by pct desc
    catalog = _catalog_lookup()
    groups: dict[tuple[str, str], list] = {}
    for r in rows:
        if r.model is None:
            continue  # devices without a known model are excluded from the grid
        groups.setdefault((r.model, r.metric), []).append(r)

    out: list[HeatmapCell] = []
    for (model, metric), items in sorted(groups.items()):
        pcts = [it.pct for it in items if it.pct is not None]
        max_pct = max(pcts) if pcts else None
        items_sorted = sorted(
            items,
            key=lambda x: (x.pct is None, -(x.pct or 0.0)),
        )
        top = [
            HeatmapDeviceTop(
                device_id=it.device_id,
                device_name=it.device_name,
                current=it.current_value,
                max=it.max_value,
                pct=it.pct,
            )
            for it in items_sorted[:top_n]
        ]
        spec = catalog.get(metric)
        out.append(
            HeatmapCell(
                model=model,
                metric=metric,
                category=spec.category if spec else "unknown",
                metric_description=spec.description if spec else metric,
                max_pct=max_pct,
                device_count=len(items),
                top_devices=top,
            )
        )
    return out


# ---------- /capacity/table ----------


@router.get("/table", response_model=TableResponse)
def get_table(
    model: str | None = Query(default=None),
    metric: str | None = Query(default=None),
    device_group: str | None = Query(default=None),
    template_stack: str | None = Query(default=None),
    pct_min: float | None = Query(default=None, ge=0, le=100),
    pct_max: float | None = Query(default=None, ge=0, le=100),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Flat (device, metric) table view with filters + pagination.

    Default sort is by pct DESC NULLS LAST — the operator's "what's
    on fire" view. The result-count cap of 500 fits comfortably in a
    virtualized table; the limit param lets a small UI tweak raise it
    if needed. Phase 15 will introduce server-side query cursors if
    the fleet ever pushes past the comfort range.
    """
    latest = _latest_samples_query(db).subquery()
    q = (
        select(
            Device.id.label("device_id"),
            Device.name.label("device_name"),
            Device.serial,
            Device.ip_address,
            Device.model,
            Device.current_version,
            Device.device_group,
            Device.template_stack,
            latest.c.metric,
            latest.c.current_value,
            latest.c.max_value,
            latest.c.pct,
            latest.c.ts,
        )
        .join(Device, Device.id == latest.c.device_id)
    )
    if model is not None:
        q = q.where(Device.model == model)
    if metric is not None:
        q = q.where(latest.c.metric == metric)
    if device_group is not None:
        q = q.where(Device.device_group == device_group)
    if template_stack is not None:
        q = q.where(Device.template_stack == template_stack)
    if pct_min is not None:
        q = q.where(latest.c.pct >= pct_min)
    if pct_max is not None:
        q = q.where(latest.c.pct <= pct_max)

    # Sort: pct DESC NULLS LAST. Stable secondary sort by device_id for
    # determinism (matters for paginated requests).
    q = q.order_by(latest.c.pct.desc().nullslast(), Device.id.asc())

    # Get total count before applying limit/offset. Single COUNT query
    # against the same filters — cheap on the indexed sample table.
    total = db.execute(
        select(func.count()).select_from(q.subquery())
    ).scalar_one()

    q = q.limit(limit).offset(offset)
    rows = db.execute(q).all()
    catalog = _catalog_lookup()

    out_rows: list[TableRow] = []
    for r in rows:
        spec = catalog.get(r.metric)
        out_rows.append(
            TableRow(
                device_id=r.device_id,
                device_name=r.device_name,
                serial=r.serial,
                ip_address=r.ip_address,
                model=r.model,
                software_version=r.current_version,
                device_group=r.device_group,
                template_stack=r.template_stack,
                metric=r.metric,
                metric_description=spec.description if spec else r.metric,
                category=spec.category if spec else "unknown",
                current=r.current_value,
                max=r.max_value,
                pct=r.pct,
                predicted_date=None,  # see schema comment
                last_sample_at=r.ts,
            )
        )
    return TableResponse(rows=out_rows, total=total)


# ---------- /capacity/trend/{device_id}/{metric} ----------


@router.get("/trend/{device_id}/{metric}", response_model=TrendResponse)
def get_trend(
    device_id: int,
    metric: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Time-series for one (device, metric) with optional prediction.

    `days` is the lookback window — default 30 days matches the
    operator screenshot's "Time: Past 30 Days" selector. The prediction
    is a linear regression over the same window; if the data is
    insufficient or shrinking, the prediction field reports that
    honestly (method="insufficient_data" / "decreasing") rather than
    extrapolating bad signal.
    """
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows = db.execute(
        select(Sample.ts, Sample.current_value, Sample.max_value, Sample.pct)
        .where(
            Sample.device_id == device_id,
            Sample.metric == metric,
            Sample.ts >= start,
            Sample.ts <= end,
        )
        .order_by(Sample.ts.asc())
    ).all()

    samples = [
        TrendSample(ts=r.ts, current=r.current_value, pct=r.pct) for r in rows
    ]

    # Use the latest known max_value (max can change on a PAN-OS upgrade
    # that lifts a platform limit; pick the most recent sample's value
    # rather than assuming it's stable across the window).
    max_value = rows[-1].max_value if rows else None

    prediction = _predict_max_date(
        [(r.ts, r.current_value) for r in rows],
        max_value,
    )

    catalog = _catalog_lookup()
    spec = catalog.get(metric)

    return TrendResponse(
        device_id=device.id,
        device_name=device.name,
        model=device.model,
        metric=metric,
        metric_description=spec.description if spec else metric,
        max_value=max_value,
        samples=samples,
        prediction=prediction,
    )
