"""Resolver for the runtime polling config (#89 PR-4).

`get_polling_config(db)` returns the singleton PollingConfig row — the
dispatcher calls it each tick so operator edits take effect within one tick.
Migration 0012 seeds the row, so it normally always exists; the transient
fallback (built from the config.py env defaults, NOT persisted) means polling
never crashes even if the row is somehow absent (e.g. a test DB that skipped
the seed).
"""

from __future__ import annotations

from app.capacity.models.polling_config import PollingConfig
from app.config import get_settings

_SINGLETON_ID = 1


def get_polling_config(db) -> PollingConfig:
    row = db.get(PollingConfig, _SINGLETON_ID)
    if row is not None:
        return row
    s = get_settings()
    # Transient default — intentionally NOT added to the session (avoids a
    # write race between the dispatcher and the admin route on first use).
    return PollingConfig(
        id=_SINGLETON_ID,
        system_interval_seconds=s.POLL_SYSTEM_INTERVAL_SECONDS or s.POLL_INTERVAL_SECONDS,
        config_interval_seconds=s.POLL_CONFIG_INTERVAL_SECONDS,
        max_concurrency_per_panorama=s.POLL_MAX_CONCURRENCY_PER_PANORAMA,
        device_retry_backoff_seconds=s.POLL_DEVICE_RETRY_BACKOFF_SECONDS,
    )
