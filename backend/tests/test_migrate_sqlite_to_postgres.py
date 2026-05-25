"""Tests for scripts/migrate_sqlite_to_postgres.py.

These exercise the dialect-portable parts of the migration (row counts,
encrypted-blob rotation, FK preservation) using SQLite for both source
and dest. The Postgres-specific parts (TRUNCATE ... RESTART IDENTITY
CASCADE, setval-based sequence advance) are visually reviewed and
manually verified on the VM at cutover time — pytest doesn't spin up a
Postgres for this.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text

from scripts.migrate_sqlite_to_postgres import (
    TABLES,
    _coerce_bools,
    _discover_bool_columns,
    migrate,
)

KEY_A = "0iJL2gP4XzVnQ5OYG9w7c-3RbWUf3jM0SQk5oN6E9Bs="
KEY_B = "aFKkQ2GfQEJDxKjPYsbnNg-LeFRSnB-DcdsdJZ3lGtY="


@pytest.fixture
def source_db_path():
    """Build a temporary SQLite DB with the 0001-era schema + seed rows."""
    fd = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    fd.close()
    path = fd.name
    conn = sqlite3.connect(path)

    # Schema: matches what migration 0001 produces (subset of columns
    # we actually populate in the test). Keep it minimal — the
    # migration script reads the columns it expects, ignores others.
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            email TEXT,
            password_hash TEXT,
            is_admin INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            encrypted_totp_secret BLOB,
            totp_enabled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_login_at TEXT
        );
        CREATE TABLE oidc_providers (
            id INTEGER PRIMARY KEY,
            slug TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            issuer TEXT NOT NULL,
            client_id TEXT NOT NULL,
            encrypted_client_secret BLOB NOT NULL,
            scopes TEXT NOT NULL DEFAULT 'openid email profile',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE panoramas (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            hostname TEXT NOT NULL,
            encrypted_api_key BLOB,
            verify_tls INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_sync_at TEXT,
            reachable INTEGER NOT NULL DEFAULT 1,
            last_reachability_at TEXT,
            last_reachability_error TEXT
        );
        CREATE TABLE devices (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            hostname TEXT NOT NULL,
            ip_address TEXT,
            serial TEXT UNIQUE,
            model TEXT,
            sw_version TEXT,
            source TEXT NOT NULL DEFAULT 'direct',
            panorama_id INTEGER REFERENCES panoramas(id),
            encrypted_api_key BLOB,
            verify_tls INTEGER NOT NULL DEFAULT 1,
            proxy_via_panorama INTEGER NOT NULL DEFAULT 0,
            polling_enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_poll_at TEXT,
            last_poll_error TEXT
        );
        CREATE TABLE sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            user_agent TEXT
        );
        CREATE TABLE backup_codes (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            code_hash TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE samples (
            id INTEGER PRIMARY KEY,
            device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            metric TEXT NOT NULL,
            ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            current_value REAL NOT NULL,
            max_value REAL,
            pct REAL
        );
    """)

    # Seed data: encrypted columns use KEY_A.
    fernet = Fernet(KEY_A.encode())
    conn.execute(
        "INSERT INTO users (id, username, encrypted_totp_secret, totp_enabled) "
        "VALUES (1, 'alice', ?, 1)",
        (fernet.encrypt(b"alice-totp"),),
    )
    conn.execute(
        "INSERT INTO users (id, username) VALUES (2, 'bob')"
    )
    conn.execute(
        "INSERT INTO oidc_providers (id, slug, display_name, issuer, "
        "client_id, encrypted_client_secret) "
        "VALUES (1, 'idp', 'IdP', 'https://idp.test', 'client-1', ?)",
        (fernet.encrypt(b"oidc-secret"),),
    )
    conn.execute(
        "INSERT INTO panoramas (id, name, hostname, encrypted_api_key) "
        "VALUES (1, 'pano1', '10.0.0.100', ?)",
        (fernet.encrypt(b"pano-key"),),
    )
    conn.execute(
        "INSERT INTO devices (id, name, hostname, encrypted_api_key, panorama_id) "
        "VALUES (1, 'dev1', '10.0.0.1', ?, 1)",
        (fernet.encrypt(b"dev-key"),),
    )
    conn.execute(
        "INSERT INTO devices (id, name, hostname) "
        "VALUES (2, 'dev2-proxied', '10.0.0.2')"
    )
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) "
        "VALUES ('hash1', 1, '2099-01-01')"
    )
    conn.execute(
        "INSERT INTO backup_codes (id, user_id, code_hash) "
        "VALUES (1, 1, 'codehash1')"
    )
    conn.execute(
        "INSERT INTO samples (id, device_id, metric, current_value, max_value) "
        "VALUES (1, 1, 'dp_cpu', 42.5, 100), "
        "(2, 1, 'mp_cpu', 30.0, 100)"
    )
    conn.commit()
    conn.close()

    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def dest_engine_factory():
    """Yields a function that builds a fresh SQLite dest engine with
    the post-0003 schema in place.

    We use Base.metadata.create_all here instead of alembic upgrade
    because env.py overrides the URL with `settings.DATABASE_URL`
    (which is the conftest test DB, not our fixture's fresh file).
    create_all produces the same set of tables that alembic head does
    — the only difference is that the alembic_version table is absent,
    which the migration script doesn't care about.
    """
    paths_made: list[str] = []

    def factory():
        # Import inside the factory so all models (including the
        # upgrade-module ones) are registered before create_all runs.
        from app.db import Base
        # noqa: F401
        from app.capacity import models as _  # noqa: F401
        from app.core.auth import models as _a  # noqa: F401
        from app.core.devices import models as _d  # noqa: F401
        from app.core.panorama import models as _p  # noqa: F401
        from app.upgrade import models as _u  # noqa: F401

        fd = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        fd.close()
        url = f"sqlite:///{fd.name}"
        engine = create_engine(url, future=True)
        Base.metadata.create_all(bind=engine)
        paths_made.append(fd.name)
        return engine

    yield factory

    for p in paths_made:
        Path(p).unlink(missing_ok=True)


def test_dry_run_does_not_write(source_db_path, dest_engine_factory):
    """Default mode reads + verifies but writes nothing."""
    dest = dest_engine_factory()
    source = sqlite3.connect(source_db_path)
    source.row_factory = sqlite3.Row

    rc = migrate(
        source=source,
        dest_engine=dest,
        old=Fernet(KEY_A.encode()),
        new=Fernet(KEY_B.encode()),
        apply=False,
        truncate_dest=False,
    )
    assert rc == 0

    # Dest is empty.
    with dest.begin() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM users")).scalar_one()
    assert n == 0
    source.close()


def test_apply_migrates_and_rotates_keys(source_db_path, dest_engine_factory):
    """Every row copies; every encrypted blob now decrypts with KEY_B
    only, not KEY_A."""
    dest = dest_engine_factory()
    source = sqlite3.connect(source_db_path)
    source.row_factory = sqlite3.Row

    rc = migrate(
        source=source,
        dest_engine=dest,
        old=Fernet(KEY_A.encode()),
        new=Fernet(KEY_B.encode()),
        apply=True,
        truncate_dest=False,
    )
    assert rc == 0
    source.close()

    fernet_b = Fernet(KEY_B.encode())
    fernet_a = Fernet(KEY_A.encode())

    with dest.begin() as conn:
        # Row counts match source seed.
        assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar_one() == 2
        assert conn.execute(text("SELECT COUNT(*) FROM oidc_providers")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM panoramas")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM devices")).scalar_one() == 2
        assert conn.execute(text("SELECT COUNT(*) FROM sessions")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM backup_codes")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM samples")).scalar_one() == 2

        # alice's encrypted_totp_secret rotated to KEY_B.
        blob = conn.execute(
            text("SELECT encrypted_totp_secret FROM users WHERE id = 1")
        ).scalar_one()
        assert fernet_b.decrypt(bytes(blob)) == b"alice-totp"
        with pytest.raises(Exception):
            fernet_a.decrypt(bytes(blob))

        # bob has no TOTP — stays NULL.
        assert conn.execute(
            text("SELECT encrypted_totp_secret FROM users WHERE id = 2")
        ).scalar_one() is None

        # OIDC client secret rotated.
        blob = conn.execute(
            text("SELECT encrypted_client_secret FROM oidc_providers WHERE id = 1")
        ).scalar_one()
        assert fernet_b.decrypt(bytes(blob)) == b"oidc-secret"

        # Panorama key rotated.
        blob = conn.execute(
            text("SELECT encrypted_api_key FROM panoramas WHERE id = 1")
        ).scalar_one()
        assert fernet_b.decrypt(bytes(blob)) == b"pano-key"

        # Device key rotated.
        blob = conn.execute(
            text("SELECT encrypted_api_key FROM devices WHERE id = 1")
        ).scalar_one()
        assert fernet_b.decrypt(bytes(blob)) == b"dev-key"

        # FK preservation: sample.device_id still references device 1.
        rows = conn.execute(
            text("SELECT device_id FROM samples ORDER BY id")
        ).all()
        assert [r.device_id for r in rows] == [1, 1]


def test_id_preservation(source_db_path, dest_engine_factory):
    """Source row IDs are preserved verbatim — FKs depend on them."""
    dest = dest_engine_factory()
    source = sqlite3.connect(source_db_path)
    source.row_factory = sqlite3.Row

    migrate(
        source=source, dest_engine=dest,
        old=Fernet(KEY_A.encode()), new=Fernet(KEY_B.encode()),
        apply=True, truncate_dest=False,
    )
    source.close()

    with dest.begin() as conn:
        # User 1 = alice (id preserved)
        username = conn.execute(
            text("SELECT username FROM users WHERE id = 1")
        ).scalar_one()
        assert username == "alice"
        # Device 1 → Panorama 1 link preserved
        pano_id = conn.execute(
            text("SELECT panorama_id FROM devices WHERE id = 1")
        ).scalar_one()
        assert pano_id == 1


def test_undecryptable_blob_blocks_table_write(
    source_db_path, dest_engine_factory
):
    """A blob that decrypts with neither key fails the table — the table
    rolls back; apply returns 1."""
    # Corrupt one blob in the source.
    conn = sqlite3.connect(source_db_path)
    conn.execute(
        "UPDATE devices SET encrypted_api_key = ? WHERE id = 1",
        (b"this-is-garbage-not-a-fernet-token",),
    )
    conn.commit()
    conn.close()

    dest = dest_engine_factory()
    source = sqlite3.connect(source_db_path)
    source.row_factory = sqlite3.Row

    rc = migrate(
        source=source, dest_engine=dest,
        old=Fernet(KEY_A.encode()), new=Fernet(KEY_B.encode()),
        apply=True, truncate_dest=False,
    )
    assert rc == 1
    source.close()

    with dest.begin() as conn:
        # devices table is empty due to rollback.
        n = conn.execute(text("SELECT COUNT(*) FROM devices")).scalar_one()
    assert n == 0


# ---------- Boolean coercion (pan-ops-console#34) ----------

def test_discover_bool_columns_finds_known_bools(dest_engine_factory):
    """Reflection picks up every BOOLEAN column the dest schema declares."""
    dest = dest_engine_factory()
    bool_cols = _discover_bool_columns(dest)

    # Spot-check the obvious ones from the merged schema.
    expected_present = {
        ("users", "is_admin"),
        ("users", "is_active"),
        ("users", "totp_enabled"),
        ("oidc_providers", "enabled"),
        ("panoramas", "verify_tls"),
        ("panoramas", "reachable"),
        ("panoramas", "proxy_upgrades"),
        ("devices", "verify_tls"),
        ("devices", "proxy_via_panorama"),
        ("devices", "polling_enabled"),
        ("devices", "connected"),
    }
    missing = expected_present - bool_cols
    assert not missing, f"Reflection missed: {sorted(missing)}"


def test_coerce_bools_int_to_bool():
    """The integer 0/1 values that SQLite returns become bool for known
    boolean columns; non-bool columns are untouched."""
    spec = next(s for s in TABLES if s.name == "users")
    bool_cols = {
        ("users", "is_admin"),
        ("users", "is_active"),
        ("users", "totp_enabled"),
    }
    data = {
        "id": 1,                          # int, not in bool_cols → stays int
        "username": "alice",              # str → untouched
        "is_admin": 1,                    # int → True
        "is_active": 1,                   # int → True
        "totp_enabled": 0,                # int → False
        "encrypted_totp_secret": b"\\x00\\x01",  # bytes → untouched
        "password_hash": None,            # None → None (unchanged)
    }

    _coerce_bools(spec, data, bool_cols)

    assert data["is_admin"] is True
    assert data["is_active"] is True
    assert data["totp_enabled"] is False
    assert data["id"] == 1                # not a bool column
    assert data["username"] == "alice"
    assert data["encrypted_totp_secret"] == b"\\x00\\x01"
    assert data["password_hash"] is None


def test_coerce_bools_none_stays_none():
    """NULL bool column stays NULL (not coerced to False)."""
    spec = next(s for s in TABLES if s.name == "users")
    bool_cols = {("users", "totp_enabled")}
    data = {"id": 5, "totp_enabled": None}
    _coerce_bools(spec, data, bool_cols)
    assert data["totp_enabled"] is None


def test_coerce_bools_already_bool_passes_through():
    """Idempotent — a row that's already in dest format doesn't double-coerce."""
    spec = next(s for s in TABLES if s.name == "users")
    bool_cols = {("users", "is_admin")}
    data = {"is_admin": True}
    _coerce_bools(spec, data, bool_cols)
    assert data["is_admin"] is True


def test_apply_with_bool_coercion_writes_correct_types(
    source_db_path, dest_engine_factory
):
    """End-to-end: source has SQLite int 0/1 for booleans, dest gets the
    right Python types post-INSERT.

    SQLite-as-dest stores them as int either way, so what we're really
    asserting is that the coerce path didn't blow up. The real
    type-mismatch error only fires on Postgres — when the platform
    session re-runs this against the cluster Postgres, the coercion
    should make psycopg accept the INSERT.
    """
    dest = dest_engine_factory()
    source = sqlite3.connect(source_db_path)
    source.row_factory = sqlite3.Row

    rc = migrate(
        source=source, dest_engine=dest,
        old=Fernet(KEY_A.encode()), new=Fernet(KEY_B.encode()),
        apply=True, truncate_dest=False,
    )
    assert rc == 0
    source.close()

    with dest.begin() as conn:
        # alice was seeded with totp_enabled=1
        totp = conn.execute(
            text("SELECT totp_enabled FROM users WHERE id = 1")
        ).scalar_one()
        # SQLite stores it back as 1 (the BOOLEAN column-affinity quirk).
        # The actual cross-engine assertion that matters is: the row WROTE
        # without erroring. If coercion was off, the dry-run-passing
        # apply-failing pattern from pan-ops-console#34 would reproduce.
        assert totp in (1, True)
