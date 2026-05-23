"""OIDC route + service tests.

We don't hit a real IdP — every IdP-facing call is patched. What we're
verifying:
  - bootstrap-status surfaces configured providers
  - /oidc/<name>/login redirects to the IdP authorize URL with the
    right params (state, nonce, PKCE challenge, scope, client_id, redirect_uri)
  - /oidc/<name>/callback errors redirect to /login with the message
  - successful claim → first user becomes admin (bootstrap path)
  - successful claim with email matching an existing user → that user
    is logged in
  - successful claim that matches NOTHING → friendly error redirect
"""

from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

import pytest


# Provider env vars are set BEFORE app import. conftest already imports
# the app at test collection time, so we set them here at module scope.
os.environ["OIDC_PROVIDER_FAKE_ISSUER"] = "https://idp.test"
os.environ["OIDC_PROVIDER_FAKE_CLIENT_ID"] = "client-id-xyz"
os.environ["OIDC_PROVIDER_FAKE_CLIENT_SECRET"] = "shhhh"
os.environ["OIDC_PROVIDER_FAKE_DISPLAY_NAME"] = "Fake IdP"


@pytest.fixture(autouse=True)
def _reload_providers():
    """Pick up the env vars above each test. The module's load at import
    time may have run before these were set (depending on test order)."""
    from app.services import oidc
    oidc.load_providers()
    yield


def test_bootstrap_status_includes_oidc_provider(client):
    r = client.get("/auth/bootstrap-status")
    assert r.status_code == 200
    body = r.json()
    assert "fake" in body["oidc_providers"]


def test_oidc_login_unknown_provider_404(client):
    r = client.get("/auth/oidc/nope/login", follow_redirects=False)
    assert r.status_code == 404


def test_oidc_login_redirects_to_idp(client, monkeypatch):
    # Stub the discovery call so we don't hit the network.
    from app.services import oidc

    def fake_discover(_provider):
        return {
            "authorization_endpoint": "https://idp.test/auth",
            "token_endpoint": "https://idp.test/token",
            "jwks_uri": "https://idp.test/jwks",
        }

    monkeypatch.setattr(oidc, "discover", fake_discover)

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
    r = client.get(
        "/auth/oidc/fake/callback?error=access_denied&error_description=user+said+no",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login?oidc_error=")
    assert "user" in r.headers["location"]


def test_oidc_callback_unknown_state(client):
    """Replay protection: a state that was never issued (or already
    consumed) is rejected."""
    r = client.get(
        "/auth/oidc/fake/callback?code=anything&state=nope",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "oidc_error" in r.headers["location"]


def test_oidc_callback_bootstrap_makes_first_user_admin(client, monkeypatch):
    """First OIDC login on an empty DB creates an admin user."""
    from app.services import oidc

    monkeypatch.setattr(oidc, "discover", lambda _p: {
        "authorization_endpoint": "https://idp.test/auth",
        "token_endpoint": "https://idp.test/token",
        "jwks_uri": "https://idp.test/jwks",
    })
    # Skip the real token exchange + JWT validation. We're verifying the
    # POST-claim path: matching, provisioning, session creation.
    monkeypatch.setattr(oidc, "complete_login", lambda state, code: {
        "sub": "alice-sub-id",
        "email": "alice@example.com",
        "preferred_username": "alice",
    })
    # Seed the in-memory state dict so the route gets past its state check.
    # (complete_login is stubbed, but the route doesn't know that — it
    # passes state through to oidc.complete_login which now ignores it.)

    r = client.get(
        "/auth/oidc/fake/callback?code=abc&state=anything",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/"

    # The user should be created and the session cookie set.
    me = client.get("/auth/me").json()
    assert me["username"] == "alice"
    assert me["email"] == "alice@example.com"
    assert me["is_admin"] is True


def test_oidc_callback_invite_only_for_subsequent_users(client, monkeypatch):
    """Once any user exists, OIDC requires a matching local row.
    Unknown identity → redirect to /login with an error."""
    from app.services import oidc

    # Bootstrap a local admin first
    client.post(
        "/auth/signup-first",
        json={"username": "admin", "password": "correct horse battery staple"},
    )
    client.post("/auth/logout")

    monkeypatch.setattr(oidc, "discover", lambda _p: {})
    monkeypatch.setattr(oidc, "complete_login", lambda state, code: {
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
    # No session cookie issued
    assert client.get("/auth/me").status_code == 401


def test_oidc_callback_matches_existing_user_by_email(client, monkeypatch):
    """An invited user whose local email matches an OIDC claim gets
    logged in seamlessly."""
    from app.services import oidc

    # Admin bootstrap + invites bob with a known email
    client.post(
        "/auth/signup-first",
        json={"username": "admin", "password": "correct horse battery staple"},
    )
    client.post(
        "/users",
        json={
            "username": "bob",
            "email": "bob@example.com",
            "password": "correct horse battery staple",
        },
    )
    client.post("/auth/logout")

    monkeypatch.setattr(oidc, "discover", lambda _p: {})
    monkeypatch.setattr(oidc, "complete_login", lambda state, code: {
        "sub": "bob-sub-id",
        "email": "BOB@example.com",   # casing differs — we lowercase before match
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
