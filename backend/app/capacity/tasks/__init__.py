"""Capacity-module Celery tasks.

Three tasks share one core (`_run_poll`):

  * ``capacity.poll_all`` — every catalog metric. Not on the beat schedule
    (see #89); kept for the manual "poll now" route (`/metrics/poll`) and
    ad-hoc full sweeps.
  * ``capacity.poll_config_metrics`` — slow beat. Config-class metrics
    (object/policy counts) that only move when an operator commits config.
  * ``capacity.poll_system_metrics`` — fast beat. Everything else: live
    telemetry (CPU, memory, sessions, throughput).

Why the split (#89): the catalog is mostly config-class metrics, and those
barely change between commits. Re-reading them every few minutes spends
Panorama API budget on near-identical data. Polling config-class hourly
and live telemetry fast lets a single Panorama proxy far more devices
within its API envelope — the explicit scaling goal is to live inside one
Panorama's budget, not to ask operators to stand up more Panoramas.

Imports happen inside the functions so the Celery worker can import this
module without immediately pulling in SQLAlchemy / the catalog YAML at
module-load time — keeps `app.workers.celery_app`'s `include` list cheap.
"""

from __future__ import annotations

import logging

from app.workers.celery_app import celery

log = logging.getLogger(__name__)

# Metric categories whose values change only when an operator commits
# config. These ride the SLOW beat. The split is framed as a complement
# (system task = "not config") rather than an explicit system/traffic
# allowlist, so a metric tagged with a brand-new category in the future
# defaults to the FAST beat and is never silently left unpolled.
_CONFIG_CATEGORIES = frozenset({"config"})


def _run_poll(task, label: str, predicate) -> dict:
    """Load the catalog, keep the metrics matching `predicate`, and poll
    every enabled device for just those metrics.

    `predicate` is a `MetricSpec -> bool`. The poller core
    (`poller.poll_all`) already takes an arbitrary metric list, so the
    split is purely "which subset of the catalog do we hand it" — no
    behavior change per metric, just cadence.

    Imports happen inside the function so the Celery worker can import
    this module without pulling in SQLAlchemy / the catalog YAML at
    module-load time.
    """
    from app.capacity.services.catalog import load_catalog
    from app.capacity.services.poller import poll_all as _poll_all
    from app.capacity.services.storage import SQLAlchemySampleStore
    from app.db import SessionLocal

    all_metrics = load_catalog()
    metrics = [m for m in all_metrics if predicate(m)]
    log.info(
        "capacity.poll[%s]: %d of %d catalog metrics",
        label, len(metrics), len(all_metrics),
    )

    store = SQLAlchemySampleStore(SessionLocal)
    try:
        with SessionLocal() as db:
            _poll_all(db, metrics, store)
    except Exception:
        log.exception("capacity poll task failed (label=%s)", label)
        # Re-raise so Celery records the task as FAILURE in the result
        # backend. Beat will fire again on the next tick regardless.
        raise
    return {
        "status": "ok",
        "task_id": task.request.id,
        "label": label,
        "metric_count": len(metrics),
    }


@celery.task(name="capacity.poll_all", bind=True)
def poll_all(self) -> dict:
    """Poll every catalog metric for every enabled device.

    Manual "poll now" entry point (`/metrics/poll`) and ad-hoc full
    sweeps. NOT on the beat schedule since #89 split scheduled polling
    into config (slow) + system (fast) cadences — keeping a single beat
    entry here too would double-poll the live-telemetry metrics.

    Returns a small summary dict for the result backend. Real
    observability (per-device success/failure counts) lives in the
    sample rows themselves.
    """
    return _run_poll(self, "all", lambda m: True)


@celery.task(name="capacity.poll_config_metrics", bind=True)
def poll_config_metrics(self) -> dict:
    """Slow beat: config-class metrics (object/policy counts).

    These change only when an operator commits, so an hourly default
    keeps the data effectively fresh while cutting the bulk of per-cycle
    Panorama API calls — the headroom that lets one Panorama proxy many
    more devices (#89).
    """
    return _run_poll(self, "config", lambda m: m.category in _CONFIG_CATEGORIES)


@celery.task(name="capacity.poll_system_metrics", bind=True)
def poll_system_metrics(self) -> dict:
    """Fast beat: live telemetry (CPU, memory, sessions, throughput).

    Defined as the complement of the config-class set so any future
    catalog category rides the fast cadence by default and is never
    silently dropped from scheduled polling.
    """
    return _run_poll(self, "system", lambda m: m.category not in _CONFIG_CATEGORIES)
