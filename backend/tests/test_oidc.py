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

    from app.core.auth.services import oidc

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

    from app.core.auth.services import oidc
    monkeypatch.setattr(oidc, "discover", lambda _p: {})
    monkeypatch.setattr(oidc, "complete_login", lambda db, state, code: {
        "sub": "stranger-sub-id",
        "email": "stranger@example.com",
        "email_verified": True,  # verified, but still no invited row
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

    from app.core.auth.services import oidc
    monkeypatch.setattr(oidc, "discover", lambda _p: {})
    monkeypatch.setattr(oidc, "complete_login", lambda db, state, code: {
        "sub": "bob-sub-id",
        "email": "BOB@example.com",  # casing differs — we lowercase before match
        "email_verified": True,
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


# ---------- AppSec F-1: OIDC identity binding ----------


def _seed_invited_user(client, username, email):
    client.post(
        "/users",
        json={"username": username, "email": email, "password": STRONG},
    )


def test_oidc_unverified_email_does_not_link(client, monkeypatch):
    """F-1(a): an invited user's email must NOT be matched when the IdP
    doesn't assert email_verified — that's the account-takeover vector
    (attacker asserts a pre-invited email it doesn't own)."""
    _seed_provider(client)
    _seed_invited_user(client, "alice", "alice@example.com")
    client.post("/auth/logout")

    from app.core.auth.services import oidc
    monkeypatch.setattr(oidc, "discover", lambda _p: {})
    # Attacker controls an IdP principal asserting alice's email but the
    # IdP does NOT mark it verified (or omits the claim entirely).
    monkeypatch.setattr(oidc, "complete_login", lambda db, state, code: {
        "sub": "attacker-sub",
        "email": "alice@example.com",
        "email_verified": False,
        "preferred_username": "alice",
    })

    r = client.get(
        "/auth/oidc/fake/callback?code=abc&state=anything",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "oidc_error" in r.headers["location"]
    assert "verified" in r.headers["location"].lower()
    # No session established.
    assert client.get("/auth/me").status_code == 401


def test_oidc_missing_email_verified_claim_does_not_link(client, monkeypatch):
    """Absent email_verified is treated the same as false — strict default."""
    _seed_provider(client)
    _seed_invited_user(client, "alice", "alice@example.com")
    client.post("/auth/logout")

    from app.core.auth.services import oidc
    monkeypatch.setattr(oidc, "discover", lambda _p: {})
    monkeypatch.setattr(oidc, "complete_login", lambda db, state, code: {
        "sub": "some-sub",
        "email": "alice@example.com",  # no email_verified key at all
        "preferred_username": "alice",
    })

    r = client.get(
        "/auth/oidc/fake/callback?code=abc&state=anything",
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "oidc_error" in r.headers["location"]
    assert client.get("/auth/me").status_code == 401


def test_oidc_first_link_persists_provider_sub(client, monkeypatch, db):
    """A verified-email initial link binds (provider, sub) on the row."""
    from app.core.auth.models.user import User

    _seed_provider(client)
    _seed_invited_user(client, "carol", "carol@example.com")
    client.post("/auth/logout")

    from app.core.auth.services import oidc
    monkeypatch.setattr(oidc, "discover", lambda _p: {})
    monkeypatch.setattr(oidc, "complete_login", lambda db_, state, code: {
        "sub": "carol-sub-123",
        "email": "carol@example.com",
        "email_verified": True,
    })

    r = client.get(
        "/auth/oidc/fake/callback?code=abc&state=anything",
        follow_redirects=False,
    )
    assert r.headers["location"] == "/"
    db.expire_all()
    carol = db.query(User).filter(User.username == "carol").first()
    assert carol.oidc_provider == "fake"
    assert carol.oidc_sub == "carol-sub-123"


def test_oidc_steady_state_matches_on_sub_not_email(client, monkeypatch, db):
    """F-1(b): once linked, identity rides on (provider, sub). A later
    login whose email has CHANGED (or whose email now collides with a
    DIFFERENT invited user) still resolves to the originally-linked
    account — email is no longer the key."""
    from app.core.auth.models.user import User

    _seed_provider(client)
    _seed_invited_user(client, "dave", "dave@example.com")
    # A second, unrelated invited user whose email the attacker-ish
    # second claim will try to assert.
    _seed_invited_user(client, "victim", "victim@example.com")
    client.post("/auth/logout")

    from app.core.auth.services import oidc
    monkeypatch.setattr(oidc, "discover", lambda _p: {})

    # First login: verified email links dave-sub → dave.
    monkeypatch.setattr(oidc, "complete_login", lambda db_, state, code: {
        "sub": "dave-sub", "email": "dave@example.com", "email_verified": True,
    })
    client.get("/auth/oidc/fake/callback?code=a&state=s1", follow_redirects=False)
    client.post("/auth/logout")

    # Second login: SAME sub, but the email claim now says victim's
    # address. Must still resolve to dave (sub wins), NOT victim.
    monkeypatch.setattr(oidc, "complete_login", lambda db_, state, code: {
        "sub": "dave-sub", "email": "victim@example.com", "email_verified": True,
    })
    r = client.get("/auth/oidc/fake/callback?code=b&state=s2", follow_redirects=False)
    assert r.headers["location"] == "/"
    me = client.get("/auth/me").json()
    assert me["username"] == "dave"  # NOT victim

    # And victim's row was never touched.
    db.expire_all()
    victim = db.query(User).filter(User.username == "victim").first()
    assert victim.oidc_sub is None


def test_oidc_sub_unique_per_provider_scope(client, monkeypatch, db):
    """Same `sub` string from a DIFFERENT provider must not cross-match —
    sub is unique only within an issuer."""
    from app.core.auth.models.user import User

    _seed_provider(client)  # slug "fake"
    _seed_provider(
        client,
        slug="other",
        display_name="Other IdP",
        issuer="https://other-idp.test",
        client_id="other-client",
    )
    _seed_invited_user(client, "erin", "erin@example.com")
    client.post("/auth/logout")

    from app.core.auth.services import oidc
    monkeypatch.setattr(oidc, "discover", lambda _p: {})
    monkeypatch.setattr(oidc, "complete_login", lambda db_, state, code: {
        "sub": "shared-sub", "email": "erin@example.com", "email_verified": True,
    })
    # Link erin via provider "fake".
    client.get("/auth/oidc/fake/callback?code=a&state=s1", follow_redirects=False)
    client.post("/auth/logout")

    db.expire_all()
    erin = db.query(User).filter(User.username == "erin").first()
    assert erin.oidc_provider == "fake" and erin.oidc_sub == "shared-sub"

    # Same sub from provider "other" + an UNVERIFIED email must not link
    # to erin (different provider scope, and email gate fails anyway).
    monkeypatch.setattr(oidc, "complete_login", lambda db_, state, code: {
        "sub": "shared-sub", "email": "erin@example.com", "email_verified": False,
    })
    r = client.get("/auth/oidc/other/callback?code=b&state=s2", follow_redirects=False)
    assert "oidc_error" in r.headers["location"]
    assert client.get("/auth/me").status_code == 401
