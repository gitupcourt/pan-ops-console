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


# ---------- AppSec F-2 / F-5 / F-7 (auth hardening) ----------


def test_provider_issuer_must_be_https(client):
    """F-2: cleartext issuer is rejected at config time (422)."""
    client.post("/auth/signup-first", json={"username": "admin", "password": STRONG})
    r = client.post("/providers/oidc", json={**DEFAULT_PAYLOAD, "issuer": "http://idp.test"})
    assert r.status_code == 422
    assert "https" in r.text.lower()


def test_provider_issuer_rejects_private_host(client):
    """F-2: an issuer pointing at a private/loopback IP is rejected —
    blocks the obvious SSRF-to-internal-metadata target."""
    client.post("/auth/signup-first", json={"username": "admin", "password": STRONG})
    for bad in ("https://127.0.0.1", "https://169.254.169.254", "https://10.0.0.5"):
        r = client.post("/providers/oidc", json={**DEFAULT_PAYLOAD, "issuer": bad})
        assert r.status_code == 422, f"{bad} should be rejected"


def test_oidc_callback_failure_is_generic(client, monkeypatch):
    """F-5: a raw exception from complete_login must NOT be reflected into
    the redirect URL — it can carry internal/token-endpoint detail."""
    _seed_provider(client)
    client.post("/auth/logout")

    from app.core.auth.services import oidc
    monkeypatch.setattr(oidc, "discover", lambda _p: {})

    def _boom(db, state, code):
        raise ValueError("token endpoint https://secret-internal.local/token failed")

    monkeypatch.setattr(oidc, "complete_login", _boom)
    r = client.get("/auth/oidc/fake/callback?code=abc&state=anything", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"].lower()
    assert "oidc_error" in loc
    assert "secret-internal" not in loc  # raw detail not leaked
    assert "administrator" in loc


def test_oidc_never_bootstraps_admin(client, monkeypatch, db):
    """F-7: with zero users, an OIDC sign-in must NOT create an admin —
    bootstrap is local-only via /auth/signup-first."""
    from app.core.auth.models.user import User

    # No signup → empty users table.
    assert db.query(User).count() == 0

    from app.core.auth.services import oidc
    monkeypatch.setattr(oidc, "discover", lambda _p: {})
    monkeypatch.setattr(oidc, "complete_login", lambda db_, state, code: {
        "sub": "would-be-admin", "email": "attacker@evil.test", "email_verified": True,
    })
    r = client.get("/auth/oidc/fake/callback?code=abc&state=anything", follow_redirects=False)
    assert r.status_code == 302
    assert "oidc_error" in r.headers["location"]
    # Crucially: no user was created.
    db.expire_all()
    assert db.query(User).count() == 0
    assert client.get("/auth/me").status_code == 401


# ---------- Trusted-provider linking (Entra compatibility) ----------
#
# Microsoft Entra never sends `email_verified` (and often no `email` claim
# at all — the address arrives as preferred_username/upn). The F-1
# verified-email gate therefore locks out every Entra user. A per-provider
# `trusted_identity` opt-in (default off) restores onboarding for an IdP the
# operator controls, by matching the asserted email/UPN against a pre-invited
# row — without weakening F-1 for untrusted IdPs.


def test_provider_trusted_identity_roundtrips_through_api(client):
    """The flag persists and is returned by the admin API (default False)."""
    pid = _seed_provider(client)
    got = next(p for p in client.get("/providers/oidc").json() if p["id"] == pid)
    assert got["trusted_identity"] is False
    client.patch(f"/providers/oidc/{pid}", json={"trusted_identity": True})
    got = next(p for p in client.get("/providers/oidc").json() if p["id"] == pid)
    assert got["trusted_identity"] is True


def test_trusted_provider_links_entra_style_without_email_verified(client, monkeypatch, db):
    """Provider marked trusted links a pre-invited user by preferred_username
    (UPN) even though the IdP sends no email and no email_verified — the
    real-world Entra shape. Binds (provider, sub) for future logins."""
    from app.core.auth.models.user import User

    _seed_provider(client, trusted_identity=True)
    _seed_invited_user(client, "frank", "frank@corp.example")
    client.post("/auth/logout")

    from app.core.auth.services import oidc
    monkeypatch.setattr(oidc, "discover", lambda _p: {})
    monkeypatch.setattr(oidc, "complete_login", lambda db_, state, code: {
        "sub": "entra-frank-oid",
        "email": "",                              # Entra often omits email
        # no email_verified key at all
        "preferred_username": "Frank@Corp.Example",  # UPN; casing differs
        "name": "Frank",
    })

    r = client.get("/auth/oidc/fake/callback?code=a&state=s", follow_redirects=False)
    assert r.headers["location"] == "/", r.headers.get("location")
    assert client.get("/auth/me").json()["username"] == "frank"
    db.expire_all()
    frank = db.query(User).filter(User.username == "frank").first()
    assert frank.oidc_provider == "fake"
    assert frank.oidc_sub == "entra-frank-oid"


def test_untrusted_provider_does_not_link_on_upn(client, monkeypatch):
    """F-1 preserved: without the trusted flag, a UPN/preferred_username is
    NOT a valid link key, so Entra-style claims are rejected."""
    _seed_provider(client)  # trusted_identity defaults False
    _seed_invited_user(client, "grace", "grace@corp.example")
    client.post("/auth/logout")

    from app.core.auth.services import oidc
    monkeypatch.setattr(oidc, "discover", lambda _p: {})
    monkeypatch.setattr(oidc, "complete_login", lambda db_, state, code: {
        "sub": "grace-attempt",
        "email": "",
        "preferred_username": "grace@corp.example",
    })

    r = client.get("/auth/oidc/fake/callback?code=a&state=s", follow_redirects=False)
    assert "oidc_error" in r.headers["location"]
    assert client.get("/auth/me").status_code == 401


def test_trusted_provider_is_still_invite_only(client, monkeypatch, db):
    """Trusted provider never CREATES accounts — a UPN with no matching
    local row is still rejected with the invite message."""
    from app.core.auth.models.user import User

    _seed_provider(client, trusted_identity=True)
    client.post("/auth/logout")

    from app.core.auth.services import oidc
    monkeypatch.setattr(oidc, "discover", lambda _p: {})
    monkeypatch.setattr(oidc, "complete_login", lambda db_, state, code: {
        "sub": "nobody-sub",
        "email": "",
        "preferred_username": "nobody@corp.example",
    })

    r = client.get("/auth/oidc/fake/callback?code=a&state=s", follow_redirects=False)
    assert "oidc_error" in r.headers["location"]
    assert "invite" in r.headers["location"].lower()
    assert client.get("/auth/me").status_code == 401
    # No account was created.
    db.expire_all()
    assert db.query(User).filter(User.username == "admin").count() == 1
    assert db.query(User).count() == 1  # just the bootstrap admin


def test_trusted_provider_does_not_link_inactive_user(client, monkeypatch, db):
    """A disabled local account must not be linked even on a trusted provider."""
    from app.core.auth.models.user import User

    _seed_provider(client, trusted_identity=True)
    _seed_invited_user(client, "heidi", "heidi@corp.example")
    # Disable heidi directly (committed → visible to the app's request session).
    heidi = db.query(User).filter(User.username == "heidi").first()
    heidi.is_active = False
    db.commit()
    client.post("/auth/logout")

    from app.core.auth.services import oidc
    monkeypatch.setattr(oidc, "discover", lambda _p: {})
    monkeypatch.setattr(oidc, "complete_login", lambda db_, state, code: {
        "sub": "heidi-sub",
        "email": "",
        "preferred_username": "heidi@corp.example",
    })

    r = client.get("/auth/oidc/fake/callback?code=a&state=s", follow_redirects=False)
    assert "oidc_error" in r.headers["location"]
    assert client.get("/auth/me").status_code == 401
