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


# --- V-2: X-Forwarded-For handling ---------------------------------------


class _Req:
    """Minimal stand-in for fastapi.Request: `headers` + `client.host`."""

    def __init__(self, xff: str | None = None, peer: str = "10.0.0.9"):
        self.headers = {"x-forwarded-for": xff} if xff is not None else {}
        self.client = type("Peer", (), {"host": peer})()


def test_client_ip_uses_rightmost_trusted_hop():
    from app.core.auth.ratelimit import client_ip

    # One trusted proxy: the entry IT appended is the rightmost one.
    assert client_ip(_Req("203.0.113.7, 198.51.100.4"), trusted_hops=1) == "198.51.100.4"
    assert client_ip(_Req("198.51.100.4"), trusted_hops=1) == "198.51.100.4"


def test_client_ip_ignores_caller_supplied_left_entries():
    from app.core.auth.ratelimit import client_ip

    a = client_ip(_Req("203.0.113.1, 198.51.100.4"), trusted_hops=1)
    b = client_ip(_Req("203.0.113.2, 198.51.100.4"), trusted_hops=1)
    assert a == b == "198.51.100.4"


def test_client_ip_counts_hops_from_the_right():
    from app.core.auth.ratelimit import client_ip

    chain = "203.0.113.7, 198.51.100.4, 10.0.0.2"
    assert client_ip(_Req(chain), trusted_hops=2) == "198.51.100.4"
    assert client_ip(_Req(chain), trusted_hops=3) == "203.0.113.7"


def test_client_ip_falls_back_to_socket_peer():
    from app.core.auth.ratelimit import client_ip

    assert client_ip(_Req(None, peer="10.0.0.9"), trusted_hops=1) == "10.0.0.9"
    # Header shorter than the trusted chain: not our proxies, don't trust it.
    assert client_ip(_Req("203.0.113.7", peer="10.0.0.9"), trusted_hops=2) == "10.0.0.9"
    # Zero hops: header ignored outright.
    assert client_ip(_Req("203.0.113.7", peer="10.0.0.9"), trusted_hops=0) == "10.0.0.9"


def test_login_throttle_survives_varying_forwarded_for(client):
    """V-2: a caller-supplied leftmost X-Forwarded-For entry must not
    carve out a fresh budget per request. With the default single trusted
    hop the key is the RIGHTMOST entry, which the caller does not control
    behind the proxy."""
    for i in range(11):
        client.post(
            "/auth/login",
            json={"username": "bob", "password": "wrong"},
            headers={"X-Forwarded-For": f"203.0.113.{i}, 198.51.100.4"},
        )
    r = client.post(
        "/auth/login",
        json={"username": "bob", "password": "wrong"},
        headers={"X-Forwarded-For": "203.0.113.99, 198.51.100.4"},
    )
    assert r.status_code == 429
