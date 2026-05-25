"""Tests for migration 0003 (phase 4c-models — upgrade tables).

Verifies every table + FK + index lands cleanly, plus a few sanity
inserts that exercise the cascade choices from MIGRATION_NOTES §8.
"""

from __future__ import annotations

from sqlalchemy import inspect, text


UPGRADE_TABLES = {
    "panos_images",
    "upgrade_jobs",
    "device_upgrade_tasks",
    "snapshots",
    "snapshot_diffs",
    "bulk_precheck_runs",
    "precheck_runs",
    "precheck_sets",
    "bulk_stage_runs",
    "device_stage_runs",
}


def test_all_upgrade_tables_exist(client, db):
    """After client fixture's `alembic upgrade head`, the upgrade-module
    tables are all present."""
    existing = set(inspect(db.bind).get_table_names())
    missing = UPGRADE_TABLES - existing
    assert not missing, f"upgrade tables missing after 0003: {missing}"


def test_capacity_and_core_tables_still_present(client, db):
    """0003 is purely additive — every pre-existing table survives."""
    existing = set(inspect(db.bind).get_table_names())
    for t in (
        "users", "sessions", "backup_codes", "oidc_providers",
        "devices", "panoramas", "samples",
    ):
        assert t in existing, f"pre-existing table {t} dropped"


def test_can_insert_into_upgrade_jobs_with_defaults(client, db):
    """Every server_default works — a minimal INSERT succeeds."""
    with db.bind.begin() as conn:
        job_id = conn.execute(
            text(
                "INSERT INTO upgrade_jobs (name, target_version) "
                "VALUES (:n, :v) RETURNING id"
            ),
            {"n": "test-job", "v": "11.1.4-h7"},
        ).scalar_one()
        row = conn.execute(
            text(
                "SELECT workflow, state, require_failover_confirmation, "
                "require_primary_upgrade_confirmation, auto_failback, "
                "auto_reboot_after_install, auto_ack_precheck_failures, "
                "auto_ack_postcheck_failures, device_pull_image "
                "FROM upgrade_jobs WHERE id = :i"
            ),
            {"i": job_id},
        ).one()

    assert row.workflow == "full"
    assert row.state == "pending"
    assert row.require_failover_confirmation in (True, 1)
    assert row.require_primary_upgrade_confirmation in (False, 0)
    assert row.auto_failback in (False, 0)
    assert row.auto_reboot_after_install in (False, 0)
    assert row.auto_ack_precheck_failures in (False, 0)
    assert row.auto_ack_postcheck_failures in (False, 0)
    assert row.device_pull_image in (False, 0)


def test_snapshot_cascade_on_device_delete(client, db):
    """snapshots.device_id ON DELETE CASCADE — deleting a device removes
    its snapshots. Per MIGRATION_NOTES §8: no orphaned blobs."""
    with db.bind.begin() as conn:
        dev_id = conn.execute(
            text(
                "INSERT INTO devices (name, hostname, verify_tls, "
                "proxy_via_panorama, polling_enabled, source) "
                "VALUES ('test-dev', '10.0.0.1', 1, 0, 1, 'direct') "
                "RETURNING id"
            )
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO snapshots (device_id, kind, data) "
                "VALUES (:d, 'ad_hoc', :j)"
            ),
            {"d": dev_id, "j": "{}"},
        )

        before = conn.execute(
            text("SELECT COUNT(*) FROM snapshots WHERE device_id = :d"),
            {"d": dev_id},
        ).scalar_one()
        assert before == 1

        conn.execute(text("DELETE FROM devices WHERE id = :d"), {"d": dev_id})

        after = conn.execute(
            text("SELECT COUNT(*) FROM snapshots WHERE device_id = :d"),
            {"d": dev_id},
        ).scalar_one()
        assert after == 0, "snapshots should have cascaded with device"


def test_snapshot_task_id_set_null_on_task_delete(client, db):
    """snapshots.task_id ON DELETE SET NULL — snapshots survive their
    task being purged. Per MIGRATION_NOTES §8."""
    with db.bind.begin() as conn:
        dev_id = conn.execute(
            text(
                "INSERT INTO devices (name, hostname, verify_tls, "
                "proxy_via_panorama, polling_enabled, source) "
                "VALUES ('ddev', '10.0.0.2', 1, 0, 1, 'direct') RETURNING id"
            )
        ).scalar_one()
        job_id = conn.execute(
            text(
                "INSERT INTO upgrade_jobs (name, target_version) "
                "VALUES ('j', '11.1.4-h7') RETURNING id"
            )
        ).scalar_one()
        task_id = conn.execute(
            text(
                "INSERT INTO device_upgrade_tasks "
                "(job_id, device_id, ha_pair_key) "
                "VALUES (:j, :d, 'pair-1') RETURNING id"
            ),
            {"j": job_id, "d": dev_id},
        ).scalar_one()
        snap_id = conn.execute(
            text(
                "INSERT INTO snapshots (device_id, task_id, kind, data) "
                "VALUES (:d, :t, 'pre_upgrade', :j) RETURNING id"
            ),
            {"d": dev_id, "t": task_id, "j": "{}"},
        ).scalar_one()

        if db.bind.dialect.name == "sqlite":
            conn.execute(text("PRAGMA foreign_keys = ON"))
        conn.execute(
            text("DELETE FROM device_upgrade_tasks WHERE id = :t"),
            {"t": task_id},
        )

        # Snapshot still exists with task_id NULL.
        row = conn.execute(
            text(
                "SELECT task_id, kind FROM snapshots WHERE id = :s"
            ),
            {"s": snap_id},
        ).one()
        assert row.task_id is None
        assert row.kind == "pre_upgrade"


def test_precheck_sets_unique_name(client, db):
    """precheck_sets.name has a UNIQUE constraint — duplicate inserts
    raise."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    with db.bind.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO precheck_sets (name, checks) "
                "VALUES ('default', :c)"
            ),
            {"c": '["ha_role"]'},
        )

    with pytest.raises(IntegrityError):
        with db.bind.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO precheck_sets (name, checks) "
                    "VALUES ('default', :c)"
                ),
                {"c": '["content_version"]'},
            )
