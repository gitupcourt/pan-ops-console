"""Tiny in-process fixed-window rate limiter for auth endpoints (AppSec F-3).

Scope: brute-force / credential-stuffing throttling on the
unauthenticated, network-reachable auth surface — password login, OIDC
callback, TOTP + backup-code verification.

Design choices (proportionate to a single-replica deployment):
  - In-process dict, not Redis. The backend runs one replica today; an
    in-memory window is correct for that and adds no new failure mode on
    the login path (a Redis blip can't lock everyone out). When the
    backend scales past one replica (phase 15), swap `_hit` for a
    Redis INCR+EXPIRE so the window is shared — the call sites don't
    change. This is deliberately the simplest thing that bites.
  - Fixed window, not sliding/token-bucket. Coarser but trivial to
    reason about and to reset in tests. The goal is "stop thousands of
    guesses," not precise fairness.
  - Keyed per (client-ip, account) for login so one attacker IP can't
    grind a single account, and a shared NAT can't lock out everyone —
    each (ip, username) pair gets its own budget.

Exceeding the budget raises HTTP 429 with a Retry-After header.
"""

from __future__ import annotations

import threading
import time

from fastapi import HTTPException, Request, status

from app.config import get_settings

_lock = threading.Lock()
# key -> (window_start_monotonic, count)
_buckets: dict[str, tuple[float, int]] = {}
# Opportunistic cap so a flood of distinct keys can't grow the dict
# without bound. Far above any legitimate working set.
_MAX_KEYS = 10_000


def client_ip(request: Request, *, trusted_hops: int | None = None) -> str:
    """Client address for rate-limit keying (V-2).

    Only the X-Forwarded-For entries appended by our own reverse proxies
    are trusted. Each hop appends the peer it saw, so with N trusted hops
    the client is the N-th entry from the RIGHT; everything further left
    arrived from the caller and is never used for keying. If the header is
    missing or shorter than the trusted chain, the request did not come
    through the expected proxies and the socket peer is used instead.
    `trusted_hops` overrides the TRUSTED_PROXY_HOPS setting (tests)."""
    hops = get_settings().TRUSTED_PROXY_HOPS if trusted_hops is None else trusted_hops
    peer = request.client.host if request.client else "unknown"
    if hops <= 0:
        return peer
    xff = request.headers.get("x-forwarded-for", "")
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if len(parts) < hops:
        return peer
    return parts[-hops]


def hit(key: str, *, limit: int, window_s: int) -> None:
    """Count one attempt against `key`. Raise 429 once it exceeds `limit`
    within `window_s`. Call this BEFORE doing the expensive/sensitive
    work so a throttled caller is cheap to reject."""
    now = time.monotonic()
    with _lock:
        if len(_buckets) > _MAX_KEYS:
            # Drop everything whose window has fully elapsed — cheap GC.
            stale = [k for k, (start, _) in _buckets.items() if now - start > window_s]
            for k in stale:
                _buckets.pop(k, None)
        start, count = _buckets.get(key, (now, 0))
        if now - start > window_s:
            start, count = now, 0
        count += 1
        _buckets[key] = (start, count)
        if count > limit:
            retry = max(1, int(window_s - (now - start)))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many attempts; please wait and try again",
                headers={"Retry-After": str(retry)},
            )


def reset() -> None:
    """Clear all windows. Used by tests for isolation; never called in
    normal operation."""
    with _lock:
        _buckets.clear()
