"""AppSec F-3: auth rate limiting.

The in-process limiter is reset between tests by the autouse
`_reset_rate_limiter` fixture in conftest, so these counts are
deterministic regardless of test order.
"""

from __future__ import annotations


def test_login_throttles_after_limit(client):
    """11th wrong-credential attempt for the same (ip, username) → 429
    with a Retry-After header. The first 10 pass through to 401."""
    for i in range(10):
        r = client.post(
            "/auth/login", json={"username": "bob", "password": "wrong"}
        )
        assert r.status_code == 401, f"attempt {i} unexpectedly {r.status_code}"

    r = client.post("/auth/login", json={"username": "bob", "password": "wrong"})
    assert r.status_code == 429
    assert "retry-after" in {k.lower() for k in r.headers}


def test_login_throttle_is_per_username(client):
    """Hammering one username must not throttle a different one — the
    budget is per (ip, username), so a shared NAT can't lock everyone
    out and an attacker can't DoS a victim's login by exhausting it."""
    for _ in range(11):
        client.post("/auth/login", json={"username": "bob", "password": "wrong"})

    # bob is now throttled…
    assert (
        client.post("/auth/login", json={"username": "bob", "password": "wrong"}).status_code
        == 429
    )
    # …but alice is not.
    assert (
        client.post("/auth/login", json={"username": "alice", "password": "wrong"}).status_code
        == 401
    )


def test_ratelimit_reset_clears_state(client):
    """Sanity: the reset helper actually clears windows (this is what
    keeps the suite isolated)."""
    from app.core.auth import ratelimit

    for _ in range(11):
        client.post("/auth/login", json={"username": "bob", "password": "wrong"})
    assert (
        client.post("/auth/login", json={"username": "bob", "password": "wrong"}).status_code
        == 429
    )
    ratelimit.reset()
    assert (
        client.post("/auth/login", json={"username": "bob", "password": "wrong"}).status_code
        == 401
    )
