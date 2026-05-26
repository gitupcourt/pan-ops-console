"""Tests for the legacy-create_all → stamp + upgrade path in app.core.migrations.

Regression coverage for pan-ops-console#43: `run_migrations()` used to
stamp HEAD on a legacy create_all DB, which made `alembic upgrade head`
a no-op and silently skipped every post-baseline migration. The fix
stamps the BASELINE revision (bottom-of-chain), then runs
`alembic upgrade head` so the post-baseline migrations actually
execute.

The bug bites in exactly one scenario:

  1. DB was built by an older app version that called
     `Base.metadata.create_all(engine)` against the *baseline-era*
     models (pre-alembic, or right at 0001).
  2. New app version with alembic + post-baseline migrations boots.
  3. `_db_has_legacy_schema_without_alembic_version()` returns True
     (marker tables present, no alembic_version).
  4. Stamp baseline + upgrade should converge the DB to HEAD.

Fresh-empty DBs (no marker tables) hit the other branch and run
`alembic upgrade head` from the base anyway, so they were never
broken by the original bug; they're covered by other tests.

Critical setup detail: the simulation needs the DB at the **baseline**
schema specifically — not at HEAD with alembic_version stripped. The
naive "drop alembic_version from a HEAD DB" makes a state that no real
deployment ever produces (HEAD-shaped tables + no version row), and
running the fix against it tries to apply 0002+ on top of an already-
0005 schema. That collides with SQLite's batch_alter_table temp-table
left behind by the first 0005 run. Correct simulation: roll the schema
back to "just 0001 ran", then drop alembic_version.
"""

from __future__ import annotations

from sqlalchemy import inspect, text


def _reset_to_legacy_baseline(engine, cfg):
    """Bring the DB to the "real legacy" state: 0001-era schema, no version row.

    Steps:
      1. Drop every SQLAlchemy-tracked table AND alembic_version. This
         strips both the schema and any version tracking.
      2. Run `command.upgrade(cfg, "0001")` to apply only the baseline
         migration. This is exactly what an old app version pinned at
         0001 (or a `create_all` against baseline-era models) would have
         produced — but driven through alembic so the result is precise
         and version-tracked.
      3. Drop alembic_version one more time so the DB now has 0001-era
         schema + nothing in alembic_version — the legacy shape the bug
         applies to.

    Importantly, this is what every test in this file relies on, and it
    matches the production scenario the platform-session Claude actually
    diagnosed in their rollback investigation.
    """
    from alembic import command

    from app.db import Base

    # 1. Wipe everything.
    Base.metadata.drop_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        # Batch mode on SQLite can leave _alembic_tmp_<table> stragglers
        # if a previous run was interrupted; clear them too so 0005's
        # batch_alter_table doesn't trip "table already exists" on the
        # next attempt.
        for tbl in ("panoramas", "devices", "samples", "users"):
            conn.execute(text(f"DROP TABLE IF EXISTS _alembic_tmp_{tbl}"))

    # 2. Apply only 0001.
    command.upgrade(cfg, "0001")

    # 3. Strip the version row so the helper detects the legacy state.
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))


def test_legacy_create_all_db_gets_stamped_at_baseline_then_upgraded(client, db):
    """End-to-end: set up the actual legacy state, run run_migrations(),
    confirm we end up at HEAD with post-baseline columns present."""
    from app.core.migrations import _alembic_config, run_migrations
    from app.db import engine

    _reset_to_legacy_baseline(engine, _alembic_config())

    inspector = inspect(engine)
    assert "alembic_version" not in inspector.get_table_names()
    # Marker tables present from the 0001 baseline migration.
    assert "devices" in inspector.get_table_names()
    assert "panoramas" in inspector.get_table_names()

    # Now run the migration helper. With the #43 fix, it should:
    #   1. Detect the legacy create_all state.
    #   2. Stamp the BASELINE revision (not head).
    #   3. Run `alembic upgrade head` so post-baseline migrations execute.
    run_migrations()

    # Verify outcome: alembic_version exists and is at the actual head.
    inspector = inspect(engine)
    assert "alembic_version" in inspector.get_table_names()
    with engine.connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()

    # The head id is what ScriptDirectory.get_current_head() returns —
    # don't hardcode the value, derive it the same way the runner does.
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    assert current == head, (
        f"Expected alembic_version={head} after run_migrations(); got {current}. "
        "Pre-#43 the legacy branch stamped HEAD WITHOUT actually running the "
        "post-baseline migrations, so the version matched but the schema "
        "lagged. We now stamp baseline + upgrade head, so both must agree "
        "with the chain's current head."
    )


def test_baseline_revision_is_resolved_dynamically(client, db):
    """The fix resolves the baseline revision via
    `ScriptDirectory.get_bases()` instead of hardcoding '0001'.

    Pin the contract: there's exactly one base revision (linear chain),
    and the runner returns its id correctly. If someone branches the
    migration tree later, _baseline_revision raises rather than picking
    a wrong base silently.

    Note the API choice: `script.get_bases()` (NOT `get_revisions("base")`,
    which returns an empty tuple — "base" is alembic's literal token for
    "before any revision," not a query for revisions whose down_revision
    is None). The wrong API landed the first CI run with a confusing
    `RuntimeError: alembic has no base revision` — the helper's loud-fail
    wired up correctly but it was firing for the wrong reason.
    """
    from app.core.migrations import _alembic_config, _baseline_revision

    cfg = _alembic_config()
    baseline = _baseline_revision(cfg)
    # As of writing the baseline is "0001". Don't hardcode that — derive
    # from the same ScriptDirectory call so this test stays correct if
    # the chain ever gets a new base prepended (unlikely but possible).
    from alembic.script import ScriptDirectory

    bases = ScriptDirectory.from_config(cfg).get_bases()
    assert len(bases) == 1, (
        "Migration tree gained multiple bases — _baseline_revision will "
        "raise in production. Pin a single base or update the helper."
    )
    assert baseline == bases[0]


def test_legacy_branch_writes_post_baseline_columns(client, db):
    """Smoke test: after the legacy stamp + upgrade path runs, columns
    that 0002+ added must exist on disk.

    Pre-#43, the legacy stamp would mark the DB at HEAD without ever
    running 0002, so `devices.current_version` (added by 0002) would
    not exist. That manifested as `OperationalError: no such column:
    devices.current_version` the first time an ORM query touched it —
    exactly the symptom the platform-session diagnostic captured.
    """
    from app.core.migrations import _alembic_config, run_migrations
    from app.db import engine

    _reset_to_legacy_baseline(engine, _alembic_config())

    run_migrations()

    # devices.current_version was added in migration 0002. Its presence
    # proves the post-baseline migration ran via the legacy stamp path.
    inspector = inspect(engine)
    dev_cols = {c["name"] for c in inspector.get_columns("devices")}
    assert "current_version" in dev_cols, (
        "post-baseline column missing — the legacy stamp path stamped HEAD "
        "without running 0002 (the pre-#43 bug)."
    )

    # panoramas.proxy_upgrades was also added in 0002.
    pano_cols = {c["name"] for c in inspector.get_columns("panoramas")}
    assert "proxy_upgrades" in pano_cols
