"""Tests for scripts/rotate_fernet.py.

The script operates via raw SQL, so the tests bypass the ORM too —
seed rows directly, run rotate(), inspect the resulting blobs with
explicit Fernet instances.

All tests run inside a fresh test DB (the `client` fixture creates +
migrates one) so the schema is real and the column types match prod.
"""

from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from scripts.rotate_fernet import rotate


# Two distinct keys, generated fresh per test run. Used to exercise FERNET
# rotation: data encrypted under KEY_A must end up encrypted under KEY_B
# after rotate() runs. Fresh-per-run so no key value lives in this repo.
KEY_A = Fernet.generate_key().decode("ascii")
KEY_B = Fernet.generate_key().decode("ascii")

# Stronger-than-policy plaintext so the password-strength check accepts it.
STRONG_PW = "correct horse battery staple"


def _seed_blobs(db, key: str) -> dict[str, int]:
    """Seed one row in each encrypted-column table, encrypted with `key`.

    Returns a dict of table → row id for later inspection.
    """
    fernet = Fernet(key.encode("ascii"))

    # Use the engine directly so we don't depend on the route handlers
    # (which would do their own encryption with the in-process key).
    ids: dict[str, int] = {}
    with db.bind.begin() as conn:
        # devices
        r = conn.execute(
            text(
                "INSERT INTO devices (name, hostname, encrypted_api_key, "
                "verify_tls, proxy_via_panorama, polling_enabled, source) "
                "VALUES (:n, :h, :k, 1, 0, 1, 'direct') RETURNING id"
            ),
            {"n": "dev1", "h": "10.0.0.1", "k": fernet.encrypt(b"dev1-api-key")},
        ).scalar_one()
        ids["devices"] = r

        # panoramas
        r = conn.execute(
            text(
                "INSERT INTO panoramas (name, hostname, encrypted_api_key, "
                "verify_tls, reachable) VALUES (:n, :h, :k, 1, 1) RETURNING id"
            ),
            {"n": "pano1", "h": "10.0.0.100", "k": fernet.encrypt(b"pano1-key")},
        ).scalar_one()
        ids["panoramas"] = r

        # oidc_providers (NOT NULL — every row has a blob)
        r = conn.execute(
            text(
                "INSERT INTO oidc_providers (slug, display_name, issuer, "
                "client_id, encrypted_client_secret, scopes, enabled) "
                "VALUES (:s, :d, :i, :c, :k, 'openid email profile', 1) "
                "RETURNING id"
            ),
            {
                "s": "idp1", "d": "IdP One", "i": "https://idp.test",
                "c": "client-xyz", "k": fernet.encrypt(b"oidc-secret-1"),
            },
        ).scalar_one()
        ids["oidc_providers"] = r

        # users with TOTP secret
        r = conn.execute(
            text(
                "INSERT INTO users (username, password_hash, is_admin, "
                "is_active, encrypted_totp_secret, totp_enabled) "
                "VALUES (:u, :p, 1, 1, :k, 1) RETURNING id"
            ),
            {
                "u": "alice", "p": "argon2-fake",
                "k": fernet.encrypt(b"alice-totp-secret"),
            },
        ).scalar_one()
        ids["users"] = r

        # users WITHOUT TOTP secret — confirms the script skips NULL blobs
        conn.execute(
            text(
                "INSERT INTO users (username, password_hash, is_admin, "
                "is_active, encrypted_totp_secret, totp_enabled) "
                "VALUES (:u, :p, 0, 1, NULL, 0)"
            ),
            {"u": "bob", "p": "argon2-fake"},
        )
    return ids


def _blob(db, table: str, pk: int, col: str) -> bytes:
    with db.bind.begin() as conn:
        return bytes(
            conn.execute(
                text(f"SELECT {col} FROM {table} WHERE id = :p"), {"p": pk}
            ).scalar_one()
        )


def test_dry_run_does_not_modify(client, db):
    """Default mode: no --apply → no writes."""
    ids = _seed_blobs(db, KEY_A)
    before = _blob(db, "devices", ids["devices"], "encrypted_api_key")

    rc = rotate(
        database_url=os.environ["DATABASE_URL"],
        old=Fernet(KEY_A.encode()),
        new=Fernet(KEY_B.encode()),
        apply=False,
    )
    assert rc == 0
    after = _blob(db, "devices", ids["devices"], "encrypted_api_key")
    assert before == after  # unchanged on disk
    # And still decryptable with the OLD key.
    assert Fernet(KEY_A.encode()).decrypt(after) == b"dev1-api-key"


def test_apply_rotates_every_blob(client, db):
    """--apply rewrites every non-NULL blob to NEW; OLD no longer
    decrypts the new bytes, NEW does."""
    ids = _seed_blobs(db, KEY_A)

    rc = rotate(
        database_url=os.environ["DATABASE_URL"],
        old=Fernet(KEY_A.encode()),
        new=Fernet(KEY_B.encode()),
        apply=True,
    )
    assert rc == 0

    # devices
    b = _blob(db, "devices", ids["devices"], "encrypted_api_key")
    assert Fernet(KEY_B.encode()).decrypt(b) == b"dev1-api-key"
    with pytest.raises(Exception):  # cryptography.fernet.InvalidToken
        Fernet(KEY_A.encode()).decrypt(b)

    # panoramas
    b = _blob(db, "panoramas", ids["panoramas"], "encrypted_api_key")
    assert Fernet(KEY_B.encode()).decrypt(b) == b"pano1-key"

    # oidc_providers
    b = _blob(db, "oidc_providers", ids["oidc_providers"], "encrypted_client_secret")
    assert Fernet(KEY_B.encode()).decrypt(b) == b"oidc-secret-1"

    # users.encrypted_totp_secret
    b = _blob(db, "users", ids["users"], "encrypted_totp_secret")
    assert Fernet(KEY_B.encode()).decrypt(b) == b"alice-totp-secret"


def test_idempotent_rerun(client, db):
    """Running --apply a second time after a successful rotation is a
    no-op for already-rotated rows (they decrypt with NEW, OLD now
    fails → script treats as already-rotated, skips)."""
    ids = _seed_blobs(db, KEY_A)
    # First run — rotates everything to KEY_B
    assert rotate(
        os.environ["DATABASE_URL"],
        Fernet(KEY_A.encode()), Fernet(KEY_B.encode()),
        apply=True,
    ) == 0
    after_first = _blob(db, "devices", ids["devices"], "encrypted_api_key")

    # Second run with same old/new pair — should skip all rows
    assert rotate(
        os.environ["DATABASE_URL"],
        Fernet(KEY_A.encode()), Fernet(KEY_B.encode()),
        apply=True,
    ) == 0
    after_second = _blob(db, "devices", ids["devices"], "encrypted_api_key")
    # Fernet ciphertext varies (nonce-based) so equality isn't expected if a
    # write occurred. The skip path means no UPDATE fired — so the bytes
    # should be byte-identical to the first run's output.
    assert after_first == after_second


def test_null_blobs_are_ignored(client, db):
    """The `bob` user has NULL encrypted_totp_secret — the WHERE clause
    filters it out, so it's neither rotated nor counted as failed."""
    _seed_blobs(db, KEY_A)
    rc = rotate(
        os.environ["DATABASE_URL"],
        Fernet(KEY_A.encode()), Fernet(KEY_B.encode()),
        apply=True,
    )
    assert rc == 0
    # bob's row stays NULL after the run
    with db.bind.begin() as conn:
        bob_blob = conn.execute(
            text("SELECT encrypted_totp_secret FROM users WHERE username='bob'")
        ).scalar_one()
    assert bob_blob is None


def test_identical_keys_refuse(monkeypatch):
    """Passing OLD==NEW is misconfiguration — exit 2 from main().

    The script raises SystemExit(2) from a pre-flight helper rather than
    returning a code, which is the right shape for a CLI but means the
    test catches the exception explicitly.
    """
    from scripts.rotate_fernet import main

    monkeypatch.setenv("OLD_FERNET_KEY", KEY_A)
    monkeypatch.setenv("NEW_FERNET_KEY", KEY_A)
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_undecryptable_row_is_flagged(client, db):
    """A row with garbage bytes (neither OLD nor NEW can decrypt) is
    counted as failed; --apply returns 1; that table's transaction
    rolls back, so the garbage stays put."""
    _seed_blobs(db, KEY_A)
    # Replace one blob with random bytes that neither key can decrypt.
    with db.bind.begin() as conn:
        conn.execute(
            text(
                "UPDATE devices SET encrypted_api_key = :g WHERE name = 'dev1'"
            ),
            {"g": b"this-is-not-a-valid-fernet-token-at-all"},
        )

    rc = rotate(
        os.environ["DATABASE_URL"],
        Fernet(KEY_A.encode()), Fernet(KEY_B.encode()),
        apply=True,
    )
    # Devices table fails → return 1, devices rolled back, other tables
    # processed before devices have already committed.
    assert rc == 1
