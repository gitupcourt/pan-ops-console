"""OIDC route + service tests (DB-backed providers).

Providers are configured via the admin API rather than env vars. Each
test seeds a provider through /providers/oidc and exercises the login
flow. Network calls to the IdP are mocked.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse


STRONG = "correct horse battery staple"
DEFAULT_PAYLOAD = {
    "slug": "fake",
    "display_name": "Fake IdP",
    "issuer": "https://idp.test",
    "client_id": "client-id-xyz",
    "client_secret": "shhhh",
    "scopes": "openid email profile",
    "enabled": True,
}


def _seed_provider(client, **overrides):
    """Bootstrap an admin and create a provider via the admin API.
    Returns the new provider id; the client is still signed in as admin."""
    client.post(
        "/auth/signup-first",
        json={"username": "admin", "password": STRONG},
    )
    payload = {**DEFAULT_PAYLOAD, **overrides}
    r = client.post("/providers/oidc", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_bootstrap_status_lists_enabled_providers(client):
    _seed_provider(client)
    client.post("/auth/logout")
    r = client.get("/auth/bootstrap-status")
    assert r.status_code == 200
    assert "fake" in r.json()["oidc_providers"]


def test_disabled_provider_hidden_from_bootstrap_status(client):
    pid = _seed_provider(client)
    client.patch(f"/providers/oidc/{pid}", json={"enabled": False})
    client.post("/auth/logout")
    assert "fake" not in client.get("/auth/bootstrap-status").json()["oidc_providers"]


def test_oidc_login_unknown_provider_404(client):
    _seed_provider(client)
    client.post("/auth/logout")
    r = client.get("/auth/oidc/nope/login", follow_redirects=False)
    assert r.status_code == 404


def test_oidc_login_redirects_to_idp(client, monkeypatch):
    _seed_provider(client)
    client.post("/auth/logout")

    from app.services import oidc

    monkeypatch.setattr(oidc, "discover", lambda _p: {
        "authorization_endpoint": "https://idp.test/auth",
        "token_endpoint": "https://idp.test/token",
        "jwks_uri": "https://idp.test/jwks",
    })

    r = client.get("/auth/oidc/fake/login", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("https://idp.test/auth?")
    qs = parse_qs(urlparse(loc).query)
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == ["client-id-xyz"]
    assert qs["code_challenge_method"] == ["S256"]
    assert "code_challenge" in qs
    assert "state" in qs
    assert "nonce" in qs
    assert qs["scope"][0].startswith("openid")


def test_oidc_callback_idp_error_redirects_to_login(client):
    _seed_provider(client)
    client.post("/auth/logout")
    r = client.get(
        "/auth/oidc/fake/callback?error=access_denied&error_description=user+said+no",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login?oidc_error=")


def test_oidc_callback_unknown_state(client):
    """Replay protection: a state that was never issued is rejected."""
    _seed_provider(client)
    client.post("/auth/logout")
    r = client.get(
        "/auth/oidc/fake/callback?code=anything&state=nope",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "oidc_error" in r.headers["location"]


def test_oidc_callback_invite_only(client, monkeypatch):
    """Existing admin signed up locally. OIDC sign-in from a stranger
    redirects to /login with the invite-required error."""
    _seed_provider(client)
    client.post("/auth/logout")

    from app.services import oidc
    monkeypatch.setattr(oidc, "discover", lambda _p: {})
    monkeypatch.setattr(oidc, "complete_login", lambda db, state, code: {
        "sub": "stranger-sub-id",
        "email": "stranger@example.com",
        "preferred_username": "stranger",
    })

    r = client.get(
        "/auth/oidc/fake/callback?code=abc&state=anything",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "oidc_error" in r.headers["location"]
    assert "invite" in r.headers["location"].lower()
    assert client.get("/auth/me").status_code == 401


def test_oidc_callback_matches_existing_user_by_email(client, monkeypatch):
    """An admin-invited user with a matching email gets signed in via OIDC."""
    _seed_provider(client)
    client.post(
        "/users",
        json={
            "username": "bob",
            "email": "bob@example.com",
            "password": STRONG,
        },
    )
    client.post("/auth/logout")

    from app.services import oidc
    monkeypatch.setattr(oidc, "discover", lambda _p: {})
    monkeypatch.setattr(oidc, "complete_login", lambda db, state, code: {
        "sub": "bob-sub-id",
        "email": "BOB@example.com",  # casing differs — we lowercase before match
        "preferred_username": "bob",
    })

    r = client.get(
        "/auth/oidc/fake/callback?code=abc&state=anything",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/"
    me = client.get("/auth/me").json()
    assert me["username"] == "bob"
