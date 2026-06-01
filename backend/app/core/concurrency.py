"""Redis-backed coordination primitives for the staggered capacity poller (#89).

Two primitives, both built on the Redis instance already used as the Celery
broker (`REDIS_URL`):

1. ``dispatch_lock`` — a short-TTL mutex so only one ``capacity.dispatch_due``
   runs at a time (beat can briefly overlap ticks; the dispatcher must not).

2. A **per-Panorama in-flight semaphore** — one self-healing counter per
   ``pano_key`` (a Panorama id, or ``"direct"``) implemented as a sorted set
   of ``token -> enqueue_epoch``. Acquire is an atomic Lua script that prunes
   stale entries (a crashed poll task that never released its slot expires by
   score), checks the cap, and adds the token. This bounds the concurrent
   proxied calls through ANY ONE Panorama independently — aggregate fleet
   throughput scales with the number of Panoramas, while no single Panorama's
   API gets hammered.

In-process tests monkeypatch these functions, so importing this module never
requires a live Redis (the client is built lazily on first real use).
"""

from __future__ import annotations

import contextlib
import time
from typing import Iterator

from app.config import get_settings

# A slot held longer than this (seconds) is assumed orphaned by a crashed
# task and is pruned on the next acquire — self-healing so the counter can
# never leak permanently. Comfortably longer than any single device poll.
DEFAULT_SLOT_STALE_SECONDS = 600

# Default dispatch-lock TTL — long enough to enqueue a tick's worth of work,
# short enough that a crashed dispatcher's lock clears before the next tick.
DEFAULT_DISPATCH_LOCK_TTL = 25

# Atomic acquire: prune stale (score <= cutoff), and if under cap, add the
# token scored at `now` and refresh the key's TTL. Returns 1 if acquired.
#   KEYS[1] = set key
#   ARGV[1] = stale cutoff epoch   ARGV[2] = cap
#   ARGV[3] = now epoch (score)    ARGV[4] = token   ARGV[5] = key ttl seconds
_ACQUIRE_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
local n = redis.call('ZCARD', KEYS[1])
if tonumber(n) < tonumber(ARGV[2]) then
  redis.call('ZADD', KEYS[1], ARGV[3], ARGV[4])
  redis.call('EXPIRE', KEYS[1], ARGV[5])
  return 1
end
return 0
"""

_client = None  # lazy redis.Redis
_acquire_script = None  # lazy registered script


def _redis():
    global _client, _acquire_script
    if _client is None:
        import redis  # local import: never needed at module import time

        _client = redis.Redis.from_url(
            get_settings().REDIS_URL, decode_responses=True
        )
        _acquire_script = _client.register_script(_ACQUIRE_LUA)
    return _client


def _slot_key(pano_key: str) -> str:
    return f"poll:inflight:{pano_key}"


def pano_key_for(device) -> str:
    """The semaphore key for a device — its Panorama id when the poll will
    actually be proxied (matches ``build_client_with_fallback`` routing), else
    ``"direct"``. Devices proxied through the same Panorama share one cap."""
    if device.proxy_via_panorama and device.panorama_id and device.serial:
        return str(device.panorama_id)
    return "direct"


@contextlib.contextmanager
def dispatch_lock(
    name: str = "capacity:dispatch",
    ttl: int = DEFAULT_DISPATCH_LOCK_TTL,
) -> Iterator[bool]:
    """Context manager yielding True iff the dispatch lock was acquired.
    Auto-expires after `ttl` so a crashed holder never wedges the schedule."""
    lock = _redis().lock(name, timeout=ttl, blocking=False)
    acquired = lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            try:
                lock.release()
            except Exception:  # noqa: BLE001 — lock may have already expired
                pass


def try_acquire_slot(
    pano_key: str,
    token: str,
    cap: int,
    *,
    stale_seconds: int = DEFAULT_SLOT_STALE_SECONDS,
) -> bool:
    """Atomically claim one in-flight slot for `pano_key` if under `cap`.
    Returns True on success (caller must `release_slot` when the poll ends)."""
    now = time.time()
    return bool(
        _acquire_script_call(
            keys=[_slot_key(pano_key)],
            args=[now - stale_seconds, cap, now, token, int(stale_seconds) + 60],
        )
    )


def _acquire_script_call(*, keys, args):
    _redis()  # ensures _acquire_script is registered
    return _acquire_script(keys=keys, args=args)


def release_slot(pano_key: str, token: str) -> None:
    """Release a previously-acquired slot. Best-effort; a missed release is
    self-healed by the stale-prune on the next acquire."""
    try:
        _redis().zrem(_slot_key(pano_key), token)
    except Exception:  # noqa: BLE001
        pass


def inflight_count(
    pano_key: str, *, stale_seconds: int = DEFAULT_SLOT_STALE_SECONDS
) -> int:
    """Current in-flight count for `pano_key`, after pruning stale entries.
    Used by the dispatcher and the PR-4 pressure telemetry."""
    client = _redis()
    key = _slot_key(pano_key)
    client.zremrangebyscore(key, "-inf", time.time() - stale_seconds)
    return int(client.zcard(key))
