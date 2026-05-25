"""Schema migration runner — wrapper around Alembic that's safe to call from
the FastAPI lifespan.

Two things this module does that bare `alembic upgrade head` doesn't:

1. **Empty-migration guard.** A blank revision file that gets applied before
   its `upgrade()` body is written silently marks itself as done. The next
   real edit to that file then doesn't run on any dev DB that already
   stamped the empty version. This is the upgrader-session land mine
   captured in MIGRATION_NOTES §3.2 — we error loud at startup before the
   silent-drift can happen.

2. **First-run stamping.** When the FastAPI app starts against a DB that
   already has the schema (because previous versions ran
   `Base.metadata.create_all`) but no `alembic_version` table, Alembic
   would try to re-create tables that exist. Detect that condition and
   `stamp head` instead of `upgrade head` — zero data movement, marks the
   DB as up-to-date.
"""

from __future__ import annotations

import ast
import importlib.util
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect as sa_inspect

from app.db import engine

log = logging.getLogger(__name__)


# Tables whose presence in a DB without alembic_version means "schema came
# from the old create_all path." If any of these exist, we stamp instead of
# upgrade. Keep this list tight — too broad and a partial DB gets stamped
# as fully migrated.
_LEGACY_CREATE_ALL_MARKER_TABLES = {"users", "devices", "panoramas", "samples"}


def _alembic_config() -> Config:
    """Locate alembic.ini relative to the backend/ root regardless of cwd."""
    # backend/app/core/migrations.py → backend/alembic.ini is 3 levels up.
    ini_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(ini_path.parent / "alembic"))
    return cfg


def _head_revision_path(cfg: Config) -> Path:
    script = ScriptDirectory.from_config(cfg)
    head_id = script.get_current_head()
    if head_id is None:
        raise RuntimeError("alembic has no head revision")
    head_script = script.get_revision(head_id)
    return Path(head_script.path)


def _migration_has_real_upgrade(path: Path) -> bool:
    """Return False if the migration's `upgrade()` body is empty / `pass` /
    docstring-only. Catches the §3.2 empty-stub trap before it ships."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
            body = node.body
            if not body:
                return False
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                return False
            # `def upgrade(): """only a docstring"""`
            if (
                len(body) == 1
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                return False
            return True
    return False  # no upgrade() defined at all


def _db_has_legacy_schema_without_alembic_version() -> bool:
    """True if the DB looks like it was created by the pre-alembic
    create_all path: marker tables exist but alembic_version doesn't."""
    inspector = sa_inspect(engine)
    existing = set(inspector.get_table_names())
    has_marker = bool(existing & _LEGACY_CREATE_ALL_MARKER_TABLES)
    has_alembic = "alembic_version" in existing
    return has_marker and not has_alembic


def run_migrations() -> None:
    """Bring the DB schema up to head. Safe to call on every startup.

    Behavior:
    - Empty DB → `alembic upgrade head` creates every table from migrations.
    - DB already at head → no-op.
    - DB has the legacy create_all schema with no alembic_version table →
      `alembic stamp head` (mark as migrated; do not re-create).
    - Head revision has an empty `upgrade()` body → raise loud; do nothing.
    """
    cfg = _alembic_config()

    head_path = _head_revision_path(cfg)
    if not _migration_has_real_upgrade(head_path):
        raise RuntimeError(
            f"Refusing to run migrations: head revision {head_path.name} has "
            "an empty upgrade() body. Fill it in before restarting — the "
            "alembic-stamps-empty-stubs trap (MIGRATION_NOTES §3.2) ate that "
            "migration if you let me proceed."
        )

    if _db_has_legacy_schema_without_alembic_version():
        log.info(
            "Detected legacy create_all schema with no alembic_version table; "
            "stamping head instead of running upgrade."
        )
        command.stamp(cfg, "head")
        return

    log.info("Running alembic upgrade head")
    command.upgrade(cfg, "head")
