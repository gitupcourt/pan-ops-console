"""Celery tasks for the Panorama module.

Today: scheduled `sync_all`, which walks every registered Panorama and
refreshes its managed-devices snapshot (Device.connected, last_seen_at,
HA state, content versions, etc.).

Before this lands, Panorama sync only fired on operator action
(manual "Sync now" button, device-import flow). That meant
`Device.connected` would go stale within minutes of a sync without
any active disconnect — turning the "stale data" UX signal into noise
(see DeviceConnectionStatus.tsx for the rant). With a beat schedule
running every PANORAMA_SYNC_INTERVAL_SECONDS, the field stays fresh
within the sync interval, and a future stale-state badge would
correctly mean "the sync itself is failing" rather than "the operator
hasn't clicked sync recently."

Cost: one `show devices all` API call per Panorama per cycle. With
default 5-min interval × 1-2 Panoramas in a typical fleet, this is
basically free. If a Panorama goes unreachable the sync writes
`reachable=False` and the next cycle retries — graceful.
"""

from __future__ import annotations

import logging

from app.workers.celery_app import celery

log = logging.getLogger(__name__)


@celery.task(name="panorama.sync_all", bind=True)
def sync_all_task(self) -> dict:
    """Scheduled refresh of every registered Panorama.

    Each Panorama's sync errors are isolated to that Panorama (the
    underlying `sync_all` catches per-Panorama exceptions and moves
    on), so one broken Panorama doesn't block fresh data for the
    rest of the fleet.

    Returns a {panorama_id: device_count_or_-1} dict for the Celery
    result backend — small enough to be cheap, useful enough to grep
    in flower / the result store when debugging "why didn't device X
    refresh."

    Imports inside the function so module-load is cheap — same
    pattern as `capacity.tasks.poll_all` and `upgrade.tasks.drive_pair_task`.
    """
    from app.core.panorama.services.sync import sync_all
    from app.db import SessionLocal

    log.info("panorama.sync_all starting: task_id=%s", self.request.id)
    with SessionLocal() as db:
        results = sync_all(db)
    log.info(
        "panorama.sync_all done: %d panorama(s) processed (-1 = errored)",
        len(results),
    )
    return {
        "status": "ok",
        "task_id": self.request.id,
        "results": results,
    }
