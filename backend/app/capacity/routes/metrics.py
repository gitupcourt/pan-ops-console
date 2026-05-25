"""Time-series read endpoints + on-demand poll trigger."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.capacity.schemas import MetricSeries, SampleRead
from app.capacity.services.catalog import load_catalog
from app.capacity.services.storage import SQLAlchemySampleStore
from app.capacity.tasks import poll_all
from app.db import SessionLocal, get_db

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
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    store = SQLAlchemySampleStore(SessionLocal)
    points = store.read_range(device_id, metric, start, end)
    return MetricSeries(
        device_id=device_id,
        metric=metric,
        samples=[
            SampleRead(ts=p.ts, current=p.current, max=p.max, pct=p.pct)
            for p in points
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
