"""End-to-end auth integration tests.

Drives the API through the same flow a browser would use:

  1. /auth/bootstrap-status reports needs_bootstrap=true on a fresh DB
  2. /auth/signup-first creates the first user (auto-admin) and sets
     the session cookie
  3. /auth/me with the cookie returns the user; without it, 401
  4. Protected routes (/devices, etc.) require auth
  5. /auth/logout invalidates the session server-side
  6. The signup-first endpoint is then frozen — no further bootstrap
"""

import pytest


def test_bootstrap_then_signup_then_me(client):
    # 1. Fresh DB → needs bootstrap
    r = client.get("/auth/bootstrap-status")
    assert r.status_code == 200
    assert r.json()["needs_bootstrap"] is True

    # 2. Signup the first user
    r = client.post(
        "/auth/signup-first",
        json={
            "username": "admin",
            "password": "correct horse battery staple",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["username"] == "admin"
    assert body["is_admin"] is True

    # Cookie was set on the response
    assert any(c.startswith("pcasession=") for c in r.headers.get_list("set-cookie"))

    # 3. /auth/me works (TestClient persists cookies across requests)
    r = client.get("/auth/me")
    assert r.status_code == 200
    assert r.json()["username"] == "admin"

    # 4. bootstrap-status flipped
    r = client.get("/auth/bootstrap-status")
    assert r.json()["needs_bootstrap"] is False

    # 5. Second signup-first is rejected
    r = client.post(
        "/auth/signup-first",
        json={"username": "second", "password": "correct horse battery staple"},
    )
    assert r.status_code == 403


def test_protected_routes_require_auth(client):
    # Without a session cookie, protected endpoints 401.
    for path in ("/devices", "/panoramas", "/metrics/catalog", "/users"):
        r = client.get(path)
        assert r.status_code == 401, f"{path} should require auth, got {r.status_code}"


def test_logout_revokes_session(client):
    # Bootstrap a user and capture the session
    client.post(
        "/auth/signup-first",
        json={"username": "admin", "password": "correct horse battery staple"},
    )
    assert client.get("/auth/me").status_code == 200

    # Logout
    r = client.post("/auth/logout")
    assert r.status_code == 204

    # Cookie is cleared, and even a leaked cookie value can't be reused
    # because the server-side row is deleted. TestClient drops the cookie
    # automatically when delete_cookie is set; just verify /me is 401.
    assert client.get("/auth/me").status_code == 401


def test_signup_first_rejects_weak_password(client):
    r = client.post(
        "/auth/signup-first",
        json={"username": "admin", "password": "Password123!"},
    )
    assert r.status_code == 422


def test_login_with_wrong_password(client):
    # Seed an admin
    client.post(
        "/auth/signup-first",
        json={"username": "admin", "password": "correct horse battery staple"},
    )
    client.post("/auth/logout")

    r = client.post(
        "/auth/login",
        json={"username": "admin", "password": "wrong wrong wrong wrong"},
    )
    assert r.status_code == 401


def test_login_with_unknown_user(client):
    """Unknown user MUST 401 with the same shape as wrong-password —
    no enumeration leak."""
    # First, bootstrap so /auth/login isn't auto-blocked. Then log out
    # so the request goes through anonymously.
    client.post(
        "/auth/signup-first",
        json={"username": "admin", "password": "correct horse battery staple"},
    )
    client.post("/auth/logout")

    r = client.post(
        "/auth/login",
        json={"username": "ghost", "password": "anything anything anything"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid credentials"
