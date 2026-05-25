"""Programmatic Alembic runner used by the FastAPI lifespan and the Celery beat startup.

Handles three states the DB might be in:

  1. Fresh / empty       -> run `alembic upgrade head` to build everything.
  2. Has tables but no
     alembic_version     -> stamp the current head (the DB is "current" by virtue
                            of an earlier create_all from before we adopted
                            Alembic). Then future migrations apply normally.
  3. Has alembic_version -> run `alembic upgrade head` to apply any new
                            migrations. No-op if already up to date.

Doing this on startup means the user never has to remember to run a migration
command — pulling and restarting the backend "just works."
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from app.db import engine

log = logging.getLogger(__name__)

# alembic.ini lives at /app/alembic.ini inside the container; resolve from this file.
_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"

# Tables we expect to exist if the DB was created by an earlier create_all run.
# We only need to spot one of them to know "this DB has app data."
_LEGACY_TABLE_HINT = "users"


def run() -> None:
    """Idempotent migration runner. Safe to call on every startup."""
    cfg = Config(str(_ALEMBIC_INI))

    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        current = ctx.get_current_revision()

        if current is None:
            inspector = inspect(conn)
            existing_tables = set(inspector.get_table_names())
            if _LEGACY_TABLE_HINT in existing_tables:
                # Pre-Alembic install — DB schema matches HEAD because earlier code
                # used Base.metadata.create_all(). Stamp it so future migrations
                # apply cleanly without trying to re-create existing tables.
                log.info("Existing tables found without Alembic version; stamping HEAD.")
                command.stamp(cfg, "head")
                return

    # Either fresh DB (alembic creates everything) or already-versioned DB
    # (alembic applies any new migrations, or no-ops if up to date).
    log.info("Running alembic upgrade head")
    command.upgrade(cfg, "head")
