"""Admin OIDC provider CRUD + the matching admin-required guard."""

from __future__ import annotations


STRONG = "correct horse battery staple"


def _admin(client):
    return client.post(
        "/auth/signup-first",
        json={"username": "admin", "password": STRONG},
    )


def _example_payload(**overrides):
    base = {
        "slug": "entra",
        "display_name": "Microsoft",
        "issuer": "https://login.microsoftonline.com/some-tenant/v2.0",
        "client_id": "abc-client-id",
        "client_secret": "super-secret-value",
        "scopes": "openid email profile",
        "enabled": True,
    }
    base.update(overrides)
    return base


def test_unauth_cannot_list(client):
    assert client.get("/providers/oidc").status_code == 401


def test_non_admin_cannot_list(client):
    _admin(client)
    client.post("/users", json={"username": "bob", "password": STRONG, "is_admin": False})
    client.post("/auth/logout")
    client.post("/auth/login", json={"username": "bob", "password": STRONG})
    assert client.get("/providers/oidc").status_code == 403


def test_admin_can_crud_provider(client):
    _admin(client)

    # List empty
    r = client.get("/providers/oidc")
    assert r.status_code == 200
    assert r.json() == []

    # Create
    r = client.post("/providers/oidc", json=_example_payload())
    assert r.status_code == 201
    body = r.json()
    assert body["slug"] == "entra"
    assert body["display_name"] == "Microsoft"
    assert body["has_client_secret"] is True
    # client_secret must NOT come back
    assert "client_secret" not in body
    assert "encrypted_client_secret" not in body
    provider_id = body["id"]

    # List has it
    r = client.get("/providers/oidc")
    assert r.status_code == 200
    assert len(r.json()) == 1

    # bootstrap-status surfaces it
    r = client.get("/auth/bootstrap-status")
    assert "entra" in r.json()["oidc_providers"]

    # Disable
    r = client.patch(f"/providers/oidc/{provider_id}", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    # No longer surfaced
    assert "entra" not in client.get("/auth/bootstrap-status").json()["oidc_providers"]

    # Re-enable + change display name (no secret rotation)
    r = client.patch(f"/providers/oidc/{provider_id}", json={
        "enabled": True, "display_name": "Microsoft Entra ID",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["display_name"] == "Microsoft Entra ID"
    assert body["has_client_secret"] is True  # untouched

    # Delete
    r = client.delete(f"/providers/oidc/{provider_id}")
    assert r.status_code == 204
    assert client.get("/providers/oidc").json() == []


def test_slug_uniqueness(client):
    _admin(client)
    r = client.post("/providers/oidc", json=_example_payload())
    assert r.status_code == 201
    r = client.post("/providers/oidc", json=_example_payload())  # same slug
    assert r.status_code == 409


def test_slug_validation(client):
    _admin(client)
    # uppercase, spaces, leading hyphen all rejected by the regex
    for bad in ("Entra", "my idp", "-entra", "entra!"):
        r = client.post("/providers/oidc", json=_example_payload(slug=bad))
        assert r.status_code == 422, f"slug {bad!r} should reject"


def test_secret_roundtrip_uses_fernet(client):
    """The stored secret should decrypt back to the value we sent, via
    the same Fernet helper that protects firewall API keys."""
    _admin(client)
    sent_secret = "the-real-deal-1234567890abcdef"
    r = client.post("/providers/oidc", json=_example_payload(client_secret=sent_secret))
    pid = r.json()["id"]

    from app.db import SessionLocal
    from app.models.oidc_provider import OIDCProvider
    from app.services.auth import decrypt_key

    with SessionLocal() as db:
        row = db.get(OIDCProvider, pid)
        assert row is not None
        assert row.encrypted_client_secret != sent_secret.encode()
        assert decrypt_key(row.encrypted_client_secret) == sent_secret


def test_oidc_login_unknown_provider_404(client):
    _admin(client)
    # No providers configured — every slug 404s
    assert client.get("/auth/oidc/nonexistent/login", follow_redirects=False).status_code == 404


def test_disabled_provider_404s_on_login(client):
    _admin(client)
    r = client.post("/providers/oidc", json=_example_payload(enabled=False))
    assert r.status_code == 201
    # Disabled → not callable
    assert client.get("/auth/oidc/entra/login", follow_redirects=False).status_code == 404
