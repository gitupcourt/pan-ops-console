"""Capacity-module Celery tasks — staggered, per-device polling (#89).

Scheduled polling is driven by a **due-time dispatcher**, not a sweep:

  * ``capacity.dispatch_due`` (beat, every ``POLL_DISPATCH_TICK_SECONDS``):
    finds devices whose class interval has elapsed, groups them by their
    managing Panorama, and — bounded by a per-Panorama in-flight semaphore,
    fair-shared round-robin across Panoramas — enqueues one
    ``poll_device_task`` per device, optimistically claiming it so it isn't
    re-selected. This spreads the fleet across each interval instead of one
    thundering herd, and bounds each Panorama's API load independently
    (aggregate throughput scales with the *number* of Panoramas).
  * ``capacity.poll_device_task`` (on the ``polling`` queue): polls ONE
    device for the due metric classes, writes samples, evaluates alerts,
    updates the per-class timestamps, releases its Panorama slot, and on
    failure rewinds the timestamp so it retries soon (not a full interval).
  * ``capacity.poll_all`` (manual "poll now" only — not scheduled): the
    original serial full sweep, kept for the `/metrics/poll` route.

Two metric classes: **config** (object/policy counts — slow cadence) and
**system** (everything else — live telemetry, fast cadence). Per-device
poll latency is dominated by serial Panorama-proxied round-trips, which is
why we stagger rather than sweep.

Imports happen inside the functions so the Celery worker can import this
module without pulling in SQLAlchemy / the catalog at module-load time.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from app.workers.celery_app import celery

log = logging.getLogger(__name__)

# Categories whose values change only on commit → the SLOW (config) class.
# Everything else is the FAST (system) class — defined as the complement so a
# future catalog category rides the fast cadence by default, never unpolled.
_CONFIG_CATEGORIES = frozenset({"config"})

# The two poll classes and their Device timestamp columns.
POLL_CLASSES = ("system", "config")
_TS_ATTR = {"system": "last_system_poll_at", "config": "last_config_poll_at"}


def _class_interval(cls: str, cfg) -> int:
    """Interval (seconds) for a poll class, from the runtime PollingConfig
    (operator-editable in Settings → Polling; #89 PR-4)."""
    return cfg.config_interval_seconds if cls == "config" else cfg.system_interval_seconds


def _metric_in_class(metric, cls: str) -> bool:
    in_config = metric.category in _CONFIG_CATEGORIES
    return in_config if cls == "config" else not in_config


# ----------------------------------------------------------------------------
# Manual full sweep (kept for the /metrics/poll route; NOT on beat).
# ----------------------------------------------------------------------------

def _run_poll(task, label: str, predicate) -> dict:
    from app.capacity.services.catalog import load_catalog
    from app.capacity.services.poller import poll_all as _poll_all
    from app.capacity.services.storage import SQLAlchemySampleStore
    from app.db import SessionLocal

    all_metrics = load_catalog()
    metrics = [m for m in all_metrics if predicate(m)]
    log.info("capacity.poll[%s]: %d of %d catalog metrics", label, len(metrics), len(all_metrics))
    store = SQLAlchemySampleStore(SessionLocal)
    try:
        with SessionLocal() as db:
            _poll_all(db, metrics, store)
    except Exception:
        log.exception("capacity poll task failed (label=%s)", label)
        raise
    return {"status": "ok", "task_id": task.request.id, "label": label, "metric_count": len(metrics)}


@celery.task(name="capacity.poll_all", bind=True)
def poll_all(self) -> dict:
    """Poll every catalog metric for every enabled device (manual "poll now").

    NOT scheduled — `capacity.dispatch_due` drives scheduled polling. Kept for
    the `/metrics/poll` route and ad-hoc full sweeps.
    """
    return _run_poll(self, "all", lambda m: True)


# ----------------------------------------------------------------------------
# Per-device poll task (runs on the `polling` queue).
# ----------------------------------------------------------------------------

@celery.task(name="capacity.poll_device_task", bind=True)
def poll_device_task(self, device_id: int, classes: list[str], pano_key: str, token: str) -> dict:
    """Poll ONE device for the given metric classes; release its Panorama slot.

    On success: write samples, evaluate alerts, stamp the per-class
    timestamps + last_poll_at. On failure: rewind the class timestamps so the
    dispatcher retries in ~POLL_DEVICE_RETRY_BACKOFF_SECONDS instead of waiting
    a full (up to 12 h) interval. The Panorama in-flight slot is always
    released in `finally` (self-healed by the semaphore's stale-prune if not).
    """
    from app.alerts.services.evaluator import evaluate_device_samples
    from app.capacity.services.catalog import load_catalog
    from app.capacity.services.poller import poll_device
    from app.capacity.services.polling_config import get_polling_config
    from app.capacity.services.storage import SQLAlchemySampleStore
    from app.core import concurrency
    from app.core.devices.models.device import Device
    from app.db import SessionLocal

    try:
        with SessionLocal() as db:
            device = db.get(Device, device_id)
            if device is None or not device.polling_enabled:
                return {"status": "gone", "device_id": device_id}

            cfg = get_polling_config(db)
            metrics = [m for m in load_catalog() if any(_metric_in_class(m, c) for c in classes)]
            now = datetime.now(timezone.utc)
            try:
                points = poll_device(db, device, metrics)
                SQLAlchemySampleStore(SessionLocal).write_samples(points)
                evaluate_device_samples(db, device.id, points)
                for cls in classes:
                    setattr(device, _TS_ATTR[cls], now)
                device.last_poll_at = now
                device.last_poll_error = None
                db.commit()
                log.info("poll_device_task %s [%s]: %d samples", device_id, ",".join(classes), len(points))
                return {"status": "ok", "device_id": device_id, "samples": len(points)}
            except Exception as exc:  # noqa: BLE001
                log.exception("poll_device_task failed for device %s", device_id)
                for cls in classes:
                    rewind = max(0, _class_interval(cls, cfg) - cfg.device_retry_backoff_seconds)
                    setattr(device, _TS_ATTR[cls], now - timedelta(seconds=rewind))
                device.last_poll_at = now
                device.last_poll_error = str(exc)[:2000]
                db.commit()
                return {"status": "error", "device_id": device_id}
    finally:
        concurrency.release_slot(pano_key, token)


# ----------------------------------------------------------------------------
# Dispatcher (beat).
# ----------------------------------------------------------------------------

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _as_utc(ts: datetime | None) -> datetime | None:
    """Coerce a DB timestamp to tz-aware UTC. SQLite returns naive datetimes
    from DateTime(timezone=True) columns; comparing those against an aware
    `now` would raise. Postgres returns aware and is unaffected."""
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


@celery.task(name="capacity.dispatch_due", bind=True)
def dispatch_due(self) -> dict:
    """Enqueue per-device poll tasks for devices whose class interval elapsed,
    bounded per Panorama and fair-shared across Panoramas. Single-flight via a
    Redis dispatch lock so overlapping beat ticks don't double-enqueue."""
    from app.capacity.services.polling_config import get_polling_config
    from app.core import concurrency
    from app.core.devices.models.device import Device
    from app.core.devices.models.enums import DeviceSource
    from app.db import SessionLocal

    enqueued = 0
    with concurrency.dispatch_lock() as got:
        if not got:
            return {"status": "skipped-locked"}
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            cfg = get_polling_config(db)
            intervals = {c: _class_interval(c, cfg) for c in POLL_CLASSES}
            cap = int(cfg.max_concurrency_per_panorama)
            devices = db.query(Device).filter(Device.polling_enabled == True).all()  # noqa: E712

            groups: dict[str, list] = {}
            for d in devices:
                due = [
                    c for c in POLL_CLASSES
                    if (ts := _as_utc(getattr(d, _TS_ATTR[c]))) is None
                    or (now - ts).total_seconds() >= intervals[c]
                ]
                if not due:
                    continue
                # Known-offline Panorama device: claim (so it isn't re-checked
                # every tick) and skip — no slot, no proxied round-trip. The
                # next Panorama sync flips `connected` and polling resumes.
                if d.source == DeviceSource.PANORAMA and not d.connected:
                    for c in due:
                        setattr(d, _TS_ATTR[c], now)
                    d.last_poll_at = now
                    d.last_poll_error = (
                        "Skipped: Panorama reports device as disconnected. "
                        "Polling resumes automatically when it reconnects."
                    )
                    continue
                groups.setdefault(concurrency.pano_key_for(d), []).append((d, due))

            # Oldest-due first within each Panorama group (NULL = never polled).
            for lst in groups.values():
                lst.sort(key=lambda t: min(_as_utc(getattr(t[0], _TS_ATTR[c])) or _EPOCH for c in t[1]))

            # Fair-share round-robin: one enqueue per group per pass, so a big
            # Panorama can't starve a small one. A group drops out of the pass
            # when it hits its per-Panorama cap or runs out of due devices.
            idx = {k: 0 for k in groups}
            active = [k for k in groups if groups[k]]
            while active:
                progressed = False
                for key in list(active):
                    lst, i = groups[key], idx[key]
                    if i >= len(lst):
                        active.remove(key)
                        continue
                    device, due = lst[i]
                    token = uuid.uuid4().hex
                    if concurrency.try_acquire_slot(key, token, cap):
                        for c in due:
                            setattr(device, _TS_ATTR[c], now)  # optimistic claim
                        poll_device_task.apply_async(args=[device.id, list(due), key, token])
                        idx[key] += 1
                        enqueued += 1
                        progressed = True
                    else:
                        active.remove(key)  # at cap this tick; try again next tick
                if not progressed:
                    break

            db.commit()

    return {"status": "ok", "enqueued": enqueued}
