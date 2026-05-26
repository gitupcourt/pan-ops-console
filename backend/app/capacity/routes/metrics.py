"""Time-series read endpoints + on-demand poll trigger."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.capacity.models.sample import Sample
from app.capacity.schemas import MetricSeries, SampleRead
from app.capacity.services.catalog import load_catalog
from app.capacity.tasks import poll_all
from app.db import get_db

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
    db: Session = Depends(get_db),
):
    """Read a (device, metric) time-series window.

    Uses ONE DB connection per request — the same one the auth chain
    already opened. PR #51 tried to fix the connection-doubling but
    missed the load-bearing detail: the router-level auth dependency
    (`dependencies=[Depends(current_user)]` in `main.py`) chains
    through `Depends(get_db)`. Once opened, the auth session HOLDS its
    pool connection for the rest of the request (SQLAlchemy 2.0
    autobegin keeps the connection attached until commit/rollback/
    close, which only happens in the `finally` block after the response
    is sent).

    Pre-PR #51 the route then ALSO opened a session via the
    `SQLAlchemySampleStore`. PR #51 swapped the store for a direct
    `with SessionLocal() as db:` — same shape, still a second
    connection. With ~17 simultaneous /series requests on every
    Dashboard load × 2 connections each = 34 against a pool of 15.
    Half of them waited the full 30s `pool_timeout` — the consistent
    30s loading time the operator reported.

    The fix: take `db: Session = Depends(get_db)` and USE it. FastAPI
    deduplicates the dependency, so the auth chain and the handler
    share the same session and the same pool connection. One
    connection per request — 17 simultaneous requests fit in a pool of
    15 with two briefly queued, each completing in milliseconds.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
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
