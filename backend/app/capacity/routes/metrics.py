"""Time-series read endpoints + on-demand poll trigger."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.capacity.models.sample import Sample
from app.capacity.schemas import MetricSeries, SampleRead
from app.capacity.services.catalog import load_catalog
from app.capacity.tasks import poll_all
from app.db import SessionLocal

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/catalog")
def get_catalog():
    """List the metrics we know how to poll."""
    return [
        {
            "name": m.name,
            "category": m.category,
            "description": m.description,
            "has_max": m.max is not None,
            "status": m.status,
        }
        for m in load_catalog()
    ]


@router.get("/{device_id}/{metric}", response_model=MetricSeries)
def get_series(
    device_id: int,
    metric: str,
    hours: int = Query(24, ge=1, le=24 * 365),
):
    """Read a (device, metric) time-series window.

    Uses ONE DB connection per request. Earlier versions of this route
    also took a `db: Session = Depends(get_db)` argument that went
    unused — combined with the SessionLocal opened inline by the store,
    every call held two connections. With ~17 metric charts on the
    Dashboard hitting concurrently on a page load, that doubled the
    pool pressure and exhausted the (5+10) connection pool, queueing
    requests for ~10s on each Loading… state.

    Goes through SQL directly here rather than through SampleStore
    because:
      (a) the store opens its own session per call (designed for the
          poller's write path, not request-scoped reads);
      (b) the read query is one SELECT — no need for the abstraction.
    The store stays available for the poller, where the per-call
    session shape is correct.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    with SessionLocal() as db:
        rows = db.execute(
            select(
                Sample.ts, Sample.current_value, Sample.max_value, Sample.pct,
            )
            .where(
                Sample.device_id == device_id,
                Sample.metric == metric,
                Sample.ts >= start,
                Sample.ts <= end,
            )
            .order_by(Sample.ts.asc())
        ).all()
    return MetricSeries(
        device_id=device_id,
        metric=metric,
        samples=[
            SampleRead(ts=r.ts, current=r.current_value, max=r.max_value, pct=r.pct)
            for r in rows
        ],
    )


@router.post("/poll/run-now", status_code=202)
def poll_now():
    """Enqueue an immediate poll cycle on the Celery worker.

    Returns the task ID so callers can poll for completion if they care.
    Before phase 2e cutover this ran synchronously in the API process via
    APScheduler.trigger_now(); now it dispatches to the worker pool and
    returns immediately. The worker runs the same poller code that beat
    triggers on schedule.
    """
    result = poll_all.delay()
    return {"status": "enqueued", "task_id": result.id}
