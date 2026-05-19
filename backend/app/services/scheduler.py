"""APScheduler wrapper that drives the poller on a fixed interval.

Kept dead simple — one background thread, one job. When we add per-device poll
intervals or jitter, this is the file that grows.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.db import SessionLocal
from app.services.catalog import load_catalog
from app.services.poller import poll_all
from app.services.storage import SQLAlchemySampleStore

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
    _scheduler.add_job(
        _tick,
        trigger=IntervalTrigger(seconds=settings.POLL_INTERVAL_SECONDS),
        id="capacity-poll",
        next_run_time=None,  # don't fire immediately on startup
    )
    _scheduler.start()
    log.info("Scheduler started — poll interval %ds", settings.POLL_INTERVAL_SECONDS)


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def trigger_now() -> None:
    """Run the poller immediately, in-thread. Used by `/poll/run-now` endpoint."""
    _tick()
