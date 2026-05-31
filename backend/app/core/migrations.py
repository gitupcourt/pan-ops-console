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
    # We invoke Alembic in-process from the FastAPI lifespan. Tell env.py
    # NOT to run fileConfig() — otherwise alembic.ini's logging config
    # clobbers the app's already-initialized root logger (root→WARN,
    # disable_existing_loggers), silently dropping every INFO log for the
    # life of the process: request logs, OIDC callback claims, and the
    # F-4 crypto/login audit trail. The standalone `alembic` CLI leaves
    # this attribute unset, so it still configures its own logging.
    cfg.attributes["configure_logging"] = False
    return cfg


def _head_revision_path(cfg: Config) -> Path:
    script = ScriptDirectory.from_config(cfg)
    head_id = script.get_current_head()
    if head_id is None:
        raise RuntimeError("alembic has no head revision")
    head_script = script.get_revision(head_id)
    return Path(head_script.path)


def _baseline_revision(cfg: Config) -> str:
    """Return the revision id of the bottom-of-chain (baseline) migration.

    Used by the legacy create_all stamp path: a DB that was built by
    the pre-alembic `Base.metadata.create_all` has exactly the schema
    the **baseline** migration produces, not the current head. Stamping
    head on such a DB makes alembic skip every later migration as a
    no-op — silent schema drift that surfaces only when ORM queries
    hit a column the DB never got (see pan-ops-console#43).

    Resolves dynamically so this stays correct as the migration tree
    grows; no need to update a hardcoded string each time a new
    baseline-touching migration is added.

    Subtle alembic API quirk this avoids: `ScriptDirectory.get_revisions("base")`
    returns an empty tuple — "base" is a literal alembic token meaning
    "before any revision," not a query for base revisions. The right call
    is `get_bases()`, which returns the revision IDs whose `down_revision`
    is None.
    """
    script = ScriptDirectory.from_config(cfg)
    bases = script.get_bases()
    if not bases:
        raise RuntimeError("alembic has no base revision")
    if len(bases) > 1:
        # Linear chain expected — multiple bases would mean someone
        # branched the migration tree, which we don't do here. Fail loud.
        raise RuntimeError(
            f"alembic has multiple base revisions ({list(bases)}); "
            "this stamp helper expects a single linear chain. Pin the right "
            "baseline manually before continuing."
        )
    return bases[0]


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
        baseline = _baseline_revision(cfg)
        log.info(
            "Detected legacy create_all schema with no alembic_version table; "
            "stamping baseline (%s) so subsequent upgrades fire normally.",
            baseline,
        )
        # Stamp the BASELINE revision (not head). The legacy create_all
        # schema matches what the baseline migration produces — anything
        # past that needs to actually run. Pre-#43 this stamped "head",
        # which made `upgrade head` a silent no-op and silently dropped
        # every post-baseline migration's column/table additions.
        command.stamp(cfg, baseline)
        # Now that the DB is anchored at baseline, do the upgrade so the
        # post-baseline migrations actually execute. Without this, the
        # caller would have to call run_migrations() twice to converge.
        log.info("Running alembic upgrade head after baseline stamp")
        command.upgrade(cfg, "head")
        return

    log.info("Running alembic upgrade head")
    command.upgrade(cfg, "head")
