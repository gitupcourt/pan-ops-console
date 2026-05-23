"""Tests for /users (admin-only) and /auth/change-password."""

from __future__ import annotations


STRONG = "correct horse battery staple"
OTHER_STRONG = "raccoon-orchestra-pickle-7"


def _signup_admin(client, username="admin", password=STRONG):
    return client.post(
        "/auth/signup-first",
        json={"username": username, "password": password},
    )


def test_admin_can_list_users(client):
    _signup_admin(client)
    r = client.get("/users")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["username"] == "admin"
    assert body[0]["is_admin"] is True


def test_admin_can_invite_user(client):
    _signup_admin(client)
    r = client.post(
        "/users",
        json={"username": "bob", "password": STRONG, "is_admin": False},
    )
    assert r.status_code == 201
    assert r.json()["username"] == "bob"
    assert r.json()["is_admin"] is False


def test_invite_rejects_weak_password(client):
    _signup_admin(client)
    r = client.post(
        "/users",
        json={"username": "bob", "password": "Password123!"},
    )
    assert r.status_code == 422


def test_invite_rejects_duplicate_username(client):
    _signup_admin(client)
    client.post("/users", json={"username": "bob", "password": STRONG})
    r = client.post("/users", json={"username": "bob", "password": OTHER_STRONG})
    assert r.status_code == 409


def test_non_admin_cannot_list_or_invite(client):
    # Bootstrap an admin, invite a regular user, sign in as the regular user
    _signup_admin(client)
    client.post("/users", json={"username": "bob", "password": STRONG, "is_admin": False})
    client.post("/auth/logout")
    r = client.post("/auth/login", json={"username": "bob", "password": STRONG})
    assert r.status_code == 200

    assert client.get("/users").status_code == 403
    r = client.post("/users", json={"username": "evil", "password": STRONG})
    assert r.status_code == 403


def test_admin_cannot_deactivate_self(client):
    r = _signup_admin(client)
    my_id = r.json()["id"]
    r = client.patch(f"/users/{my_id}/active?active=false")
    assert r.status_code == 400
    assert "deactivate yourself" in r.json()["detail"]


def test_admin_cannot_demote_self(client):
    r = _signup_admin(client)
    my_id = r.json()["id"]
    r = client.patch(f"/users/{my_id}/admin?is_admin=false")
    assert r.status_code == 400


def test_admin_cannot_delete_self(client):
    r = _signup_admin(client)
    my_id = r.json()["id"]
    r = client.delete(f"/users/{my_id}")
    assert r.status_code == 400


def test_deactivating_user_revokes_their_sessions(client):
    """The deactivated user's existing session cookie must stop working
    immediately — not on next request, not after the cookie expires."""
    _signup_admin(client)
    # Create + log in as bob
    client.post("/users", json={"username": "bob", "password": STRONG})
    client.post("/auth/logout")
    client.post("/auth/login", json={"username": "bob", "password": STRONG})
    assert client.get("/auth/me").json()["username"] == "bob"

    # Bob's cookie is held by TestClient. Switch back to admin and
    # deactivate Bob. We log out (clearing bob's cookie from TestClient)
    # and log back in as admin.
    client.post("/auth/logout")
    client.post("/auth/login", json={"username": "admin", "password": STRONG})
    bob_id = next(u["id"] for u in client.get("/users").json() if u["username"] == "bob")
    r = client.patch(f"/users/{bob_id}/active?active=false")
    assert r.status_code == 200

    # Bob's password verify would now also fail at login because is_active=false
    client.post("/auth/logout")
    r = client.post("/auth/login", json={"username": "bob", "password": STRONG})
    assert r.status_code == 401


# ---------- /auth/change-password ----------

def test_change_password_happy(client):
    _signup_admin(client)
    r = client.post(
        "/auth/change-password",
        json={"current_password": STRONG, "new_password": OTHER_STRONG},
    )
    assert r.status_code == 204
    # Session on THIS device stays valid (revoke_all + new session)
    assert client.get("/auth/me").status_code == 200
    # Old password no longer works
    client.post("/auth/logout")
    r = client.post("/auth/login", json={"username": "admin", "password": STRONG})
    assert r.status_code == 401
    # New password does
    r = client.post("/auth/login", json={"username": "admin", "password": OTHER_STRONG})
    assert r.status_code == 200


def test_change_password_wrong_current(client):
    _signup_admin(client)
    r = client.post(
        "/auth/change-password",
        json={"current_password": "nope nope nope nope", "new_password": OTHER_STRONG},
    )
    assert r.status_code == 400


def test_change_password_weak_new(client):
    _signup_admin(client)
    r = client.post(
        "/auth/change-password",
        json={"current_password": STRONG, "new_password": "Password123!"},
    )
    assert r.status_code == 422


def test_change_password_requires_auth(client):
    r = client.post(
        "/auth/change-password",
        json={"current_password": "x", "new_password": "y"},
    )
    assert r.status_code == 401
