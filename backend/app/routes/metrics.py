"""Time-series read endpoints + on-demand poll trigger."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.schemas import MetricSeries, SampleRead
from app.services import scheduler
from app.services.catalog import load_catalog
from app.services.storage import SQLAlchemySampleStore

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
            SampleRead(ts=p.ts, current_value=p.current, max_value=p.max, pct=p.pct)
            for p in points
        ],
    )


@router.post("/poll/run-now", status_code=202)
def poll_now():
    """Kick off an immediate poll cycle (synchronous for now)."""
    scheduler.trigger_now()
    return {"status": "ok"}
