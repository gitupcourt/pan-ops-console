"""TOTP enrollment, login, backup-code, and disable flows."""

from __future__ import annotations

import pyotp


STRONG = "correct horse battery staple"


def _signup(client):
    return client.post(
        "/auth/signup-first",
        json={"username": "admin", "password": STRONG},
    )


def _current_code(secret: str) -> str:
    return pyotp.TOTP(secret).now()


# ---------- setup + verify ----------

def test_setup_returns_secret_and_uri(client):
    _signup(client)
    r = client.post("/auth/totp/setup")
    assert r.status_code == 200
    body = r.json()
    assert "secret" in body
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert "pan-capacity-analyzer" in body["otpauth_uri"]


def test_setup_requires_auth(client):
    r = client.post("/auth/totp/setup")
    assert r.status_code == 401


def test_verify_enables_totp_and_returns_backup_codes(client):
    _signup(client)
    secret = client.post("/auth/totp/setup").json()["secret"]
    r = client.post("/auth/totp/verify", json={"code": _current_code(secret)})
    assert r.status_code == 200
    codes = r.json()["backup_codes"]
    assert len(codes) == 10
    # /me reflects the change
    me = client.get("/auth/me").json()
    assert me["totp_enabled"] is True


def test_verify_rejects_wrong_code(client):
    _signup(client)
    client.post("/auth/totp/setup")
    r = client.post("/auth/totp/verify", json={"code": "000000"})
    assert r.status_code == 400
    # Still not enabled
    assert client.get("/auth/me").json()["totp_enabled"] is False


def test_verify_without_setup_fails(client):
    _signup(client)
    r = client.post("/auth/totp/verify", json={"code": "123456"})
    assert r.status_code == 400


def test_setup_after_enabled_is_rejected(client):
    """Once TOTP is enabled, you must disable before re-enrolling — this
    prevents an attacker with session access from silently rotating
    secrets to lock you out."""
    _signup(client)
    secret = client.post("/auth/totp/setup").json()["secret"]
    client.post("/auth/totp/verify", json={"code": _current_code(secret)})
    r = client.post("/auth/totp/setup")
    assert r.status_code == 400


# ---------- login with TOTP ----------

def test_login_returns_needs_totp_when_enabled(client):
    _signup(client)
    secret = client.post("/auth/totp/setup").json()["secret"]
    client.post("/auth/totp/verify", json={"code": _current_code(secret)})
    client.post("/auth/logout")

    # Login with valid password but no code -> 200 with needs_totp
    r = client.post(
        "/auth/login",
        json={"username": "admin", "password": STRONG},
    )
    assert r.status_code == 200
    assert r.json() == {"needs_totp": True}
    # No session cookie set yet
    assert client.get("/auth/me").status_code == 401


def test_login_with_correct_totp_completes(client):
    _signup(client)
    secret = client.post("/auth/totp/setup").json()["secret"]
    client.post("/auth/totp/verify", json={"code": _current_code(secret)})
    client.post("/auth/logout")

    r = client.post(
        "/auth/login",
        json={"username": "admin", "password": STRONG, "totp_code": _current_code(secret)},
    )
    assert r.status_code == 200
    assert r.json()["username"] == "admin"
    assert client.get("/auth/me").status_code == 200


def test_login_with_wrong_totp_is_401(client):
    _signup(client)
    secret = client.post("/auth/totp/setup").json()["secret"]
    client.post("/auth/totp/verify", json={"code": _current_code(secret)})
    client.post("/auth/logout")

    r = client.post(
        "/auth/login",
        json={"username": "admin", "password": STRONG, "totp_code": "000000"},
    )
    assert r.status_code == 401


# ---------- backup codes ----------

def test_backup_code_logs_in_once(client):
    _signup(client)
    secret = client.post("/auth/totp/setup").json()["secret"]
    codes = client.post("/auth/totp/verify", json={"code": _current_code(secret)}).json()["backup_codes"]
    client.post("/auth/logout")

    # First use: works
    r = client.post(
        "/auth/login",
        json={"username": "admin", "password": STRONG, "totp_code": codes[0]},
    )
    assert r.status_code == 200
    client.post("/auth/logout")

    # Second use of same code: rejected (it was consumed)
    r = client.post(
        "/auth/login",
        json={"username": "admin", "password": STRONG, "totp_code": codes[0]},
    )
    assert r.status_code == 401


def test_backup_code_accepts_normalized_input(client):
    """User can type the code with or without the dash, mixed case."""
    _signup(client)
    secret = client.post("/auth/totp/setup").json()["secret"]
    codes = client.post("/auth/totp/verify", json={"code": _current_code(secret)}).json()["backup_codes"]
    code = codes[0]  # format: XXXXX-XXXXX, lowercase hex

    # Sanity: it has a dash and is lowercase
    assert "-" in code

    client.post("/auth/logout")

    # Submit without dash, uppercase — should still match
    munged = code.replace("-", "").upper()
    r = client.post(
        "/auth/login",
        json={"username": "admin", "password": STRONG, "totp_code": munged},
    )
    assert r.status_code == 200


# ---------- disable ----------

def test_disable_requires_password(client):
    _signup(client)
    secret = client.post("/auth/totp/setup").json()["secret"]
    client.post("/auth/totp/verify", json={"code": _current_code(secret)})

    r = client.post("/auth/totp/disable", json={"password": "wrong password thing"})
    assert r.status_code == 400
    assert client.get("/auth/me").json()["totp_enabled"] is True


def test_disable_clears_state_and_codes(client):
    _signup(client)
    secret = client.post("/auth/totp/setup").json()["secret"]
    codes = client.post("/auth/totp/verify", json={"code": _current_code(secret)}).json()["backup_codes"]

    r = client.post("/auth/totp/disable", json={"password": STRONG})
    assert r.status_code == 204
    assert client.get("/auth/me").json()["totp_enabled"] is False

    # Login no longer requires a code
    client.post("/auth/logout")
    r = client.post("/auth/login", json={"username": "admin", "password": STRONG})
    assert r.status_code == 200
    assert r.json()["username"] == "admin"

    # And the old backup codes are gone — even though TOTP is off, they
    # shouldn't survive into a future re-enrollment.
    client.post("/auth/logout")
    # Re-enroll
    secret2 = client.post("/auth/login", json={"username": "admin", "password": STRONG})
    # (we're back in via password; re-enroll TOTP)
    secret2 = client.post("/auth/totp/setup").json()["secret"]
    new_codes = client.post(
        "/auth/totp/verify", json={"code": _current_code(secret2)}
    ).json()["backup_codes"]
    assert set(new_codes).isdisjoint(set(codes))
