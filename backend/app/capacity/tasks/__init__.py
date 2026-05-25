"""Capacity-module Celery tasks.

Dormant in phase 2c — defined and importable, but the Celery beat
schedule that fires them is empty until phase 2e. Capacity polling
keeps running on the in-process APScheduler in
`app.capacity.services.scheduler` until cutover.

Why land the task wrapper now: the cutover then becomes a small, clean
change (add one beat_schedule entry; stop the APScheduler in lifespan;
verify) instead of a "write the task + cutover + migrate data" combo.
Smaller PRs = safer rollbacks.
"""

from __future__ import annotations

import logging

from app.workers.celery_app import celery

log = logging.getLogger(__name__)


@celery.task(name="capacity.poll_all", bind=True)
def poll_all(self) -> dict:
    """Walk every enabled device and write a sample per metric.

    Mirrors the logic in `app.capacity.services.scheduler._tick` so the
    cutover is a no-op in terms of behavior. Imports happen inside the
    function so the Celery worker can import this module without
    immediately pulling in SQLAlchemy / the catalog YAML at module-load
    time — keeps `app.workers.celery_app`'s `include` list cheap.

    Returns a small summary dict for the result backend. Real
    observability (per-device success/failure counts) lives in the
    sample rows themselves.
    """
    # Local imports — see module docstring for rationale.
    from app.capacity.services.catalog import load_catalog
    from app.capacity.services.poller import poll_all as _poll_all
    from app.capacity.services.storage import SQLAlchemySampleStore
    from app.db import SessionLocal

    metrics = load_catalog()
    store = SQLAlchemySampleStore(SessionLocal)
    try:
        with SessionLocal() as db:
            _poll_all(db, metrics, store)
    except Exception:
        log.exception("capacity.poll_all task failed")
        # Re-raise so Celery records the task as FAILURE in the result
        # backend. Beat will fire again on the next tick regardless.
        raise
    return {"status": "ok", "task_id": self.request.id}
