"""Tests for the legacy-create_all → stamp + upgrade path in app.core.migrations.

Regression coverage for pan-ops-console#43: `run_migrations()` used to
stamp HEAD on a legacy create_all DB, which made `alembic upgrade head`
a no-op and silently skipped every post-baseline migration. The fix
stamps the BASELINE revision (bottom-of-chain), then runs
`alembic upgrade head` so the post-baseline migrations actually
execute.

The bug bites in exactly one scenario:

  1. DB was built by an older app version that called
     `Base.metadata.create_all(engine)` (pre-alembic).
  2. New app version with alembic + post-baseline migrations boots.
  3. `_db_has_legacy_schema_without_alembic_version()` returns True
     (marker tables present, no alembic_version).
  4. Stamp + upgrade should converge the DB to HEAD.

Fresh-empty DBs (no marker tables) hit the other branch and run
`alembic upgrade head` from the base anyway, so they were never
broken by the original bug; they're covered by other tests.
"""

from __future__ import annotations

from sqlalchemy import inspect, text


def test_legacy_create_all_db_gets_stamped_at_baseline_then_upgraded(client, db):
    """End-to-end: simulate a legacy create_all DB, run run_migrations(),
    confirm we end up at HEAD with post-baseline columns present.

    The `client` fixture already runs the app's lifespan which calls
    `run_migrations()` — so by the time we get here the DB is at HEAD
    via the normal (non-legacy) path. We tear that down to the legacy
    shape (drop alembic_version), then call run_migrations() again to
    exercise the legacy branch.
    """
    from app.core.migrations import run_migrations
    from app.db import engine

    # Tear down to "legacy" shape: keep the tables (so the marker check
    # finds them) but drop alembic_version. After this, the DB looks
    # exactly like a pre-alembic create_all output.
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

    inspector = inspect(engine)
    assert "alembic_version" not in inspector.get_table_names()
    # Marker tables are present (they came from the lifespan-applied
    # migrations earlier in this fixture).
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
    from app.core.migrations import _alembic_config
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
    from app.core.migrations import run_migrations
    from app.db import engine

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

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
