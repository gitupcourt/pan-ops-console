"""Test fixtures.

Every test runs against a fresh, isolated SQLite DB and a one-shot
FERNET_KEY generated at import time. Nothing touches the prod / dev
cluster databases.

Two fixtures matter:

  client      — a TestClient with the app and an empty DB. Use for
                most integration-style tests (login flow, route guards).
  db          — a raw SQLAlchemy Session against the same SQLite, for
                tests that need to inspect or seed rows directly.

Test runtime sets FERNET_KEY in os.environ BEFORE importing app code,
because the app's Settings class fails fast if FERNET_KEY is missing.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Set required env vars BEFORE the first `from app...` import anywhere.
# pytest discovers test files alphabetically and may import modules
# before fixtures run, so this lives at module scope.
_TEST_DB = Path(tempfile.gettempdir()) / "pca-test.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
os.environ.setdefault(
    "FERNET_KEY",
    # Generated once for the test suite — deterministic so two runs
    # don't drift, but NOT used anywhere outside tests.
    "0iJL2gP4XzVnQ5OYG9w7c-3RbWUf3jM0SQk5oN6E9Bs=",
)
os.environ.setdefault("CATALOG_PATH", str(Path(__file__).parent / "fake_metrics.yaml"))
# Insecure cookie attribute for tests (no HTTPS in the TestClient transport)
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")


# Empty catalog so the poller has nothing to do and startup is fast.
_FAKE_CATALOG = Path(os.environ["CATALOG_PATH"])
_FAKE_CATALOG.write_text("version: 1\nmetrics: []\n", encoding="utf-8")


@pytest.fixture
def client():
    """Fresh TestClient per test, with an empty schema produced by alembic.

    Subtle: simply unlinking the .db file between tests doesn't isolate
    them, because the SQLAlchemy engine + connection pool that the app
    imported at module load is still pointing at the original file
    inode. On Linux, deleting a file with open file handles leaves the
    data alive for those handles. The reliable reset is to drop every
    table (including alembic_version) at the SQL level, then let the
    app's lifespan run `alembic upgrade head` to recreate the schema —
    which is what prod does, so the test path mirrors prod exactly.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    # Force a clean schema across every test. Drop SQLAlchemy-tracked
    # tables, then explicitly drop alembic_version (which Base doesn't
    # know about). Order matters: alembic_version has no FKs into our
    # tables so dropping it last is fine.
    from app.db import Base, engine
    Base.metadata.drop_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    """A bare SQLAlchemy session against the test DB. Useful for asserting
    "the row got written" without going through the API."""
    from app.db import SessionLocal

    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
