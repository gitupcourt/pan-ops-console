"""APScheduler wrapper that drives the poller on a fixed interval.

Kept dead simple — one background thread, one job. When we add per-device poll
intervals or jitter, this is the file that grows.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.capacity.services.catalog import load_catalog
from app.capacity.services.poller import poll_all
from app.capacity.services.storage import SQLAlchemySampleStore
from app.config import get_settings
from app.db import SessionLocal

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _tick() -> None:
    """One scheduler firing. Wrapped in try so a bad catalog can't kill the loop."""
    try:
        metrics = load_catalog()
        store = SQLAlchemySampleStore(SessionLocal)
        with SessionLocal() as db:
            poll_all(db, metrics, store)
    except Exception:
        log.exception("Poller tick failed")


def start() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    settings = get_settings()
    _scheduler = BackgroundScheduler(daemon=True)
    # Fire the first tick ~10 seconds after startup so the app has data
    # quickly without blocking the API container boot. Subsequent ticks
    # then run on the configured interval.
    #
    # Note: passing `next_run_time=None` to add_job disables the job
    # entirely — APScheduler treats it as "no scheduled future run." We
    # need an explicit datetime to arm the trigger.
    first_run = datetime.now(timezone.utc) + timedelta(seconds=10)
    _scheduler.add_job(
        _tick,
        trigger=IntervalTrigger(seconds=settings.POLL_INTERVAL_SECONDS),
        id="capacity-poll",
        next_run_time=first_run,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    log.info(
        "Scheduler started — poll interval %ds, first tick at %s",
        settings.POLL_INTERVAL_SECONDS, first_run.isoformat(),
    )


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def trigger_now() -> None:
    """Run the poller immediately, in-thread. Used by `/poll/run-now` endpoint."""
    _tick()
