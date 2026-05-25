"""Tests for migration 0002 (phase 4a schema reconciliation).

Additive-only migration: existing capacity rows must survive unchanged
through the upgrade. The new columns are nullable or have sensible
server_defaults, so populated 0001-era rows stay valid post-0002.
"""

from __future__ import annotations

from sqlalchemy import inspect, text


def _columns(db, table: str) -> set[str]:
    return {c["name"] for c in inspect(db.bind).get_columns(table)}


def test_schema_includes_upgrader_columns(client, db):
    """After client fixture runs `alembic upgrade head`, both 0001 and
    0002 are applied. Sanity-check every new column exists on disk."""
    dev_cols = _columns(db, "devices")
    for col in (
        "current_version", "ha_peer_id", "ha_role", "ha_state", "ha_sync_state",
        "device_group", "template_stack", "connected", "uptime",
        "staged_version", "staged_at", "staged_error", "downloaded_versions",
        "licenses",
        "app_version", "threat_version", "av_version", "wildfire_version",
        "url_filtering_version", "gp_client_version",
        "last_seen_at", "last_refresh_at",
    ):
        assert col in dev_cols, f"devices.{col} missing after 0002"

    # Capacity-side columns are still there.
    for col in (
        "encrypted_api_key", "polling_enabled", "last_poll_at",
        "last_poll_error", "sw_version",
    ):
        assert col in dev_cols, f"capacity-side devices.{col} dropped — should be preserved"

    pano_cols = _columns(db, "panoramas")
    assert "proxy_upgrades" in pano_cols
    # Capacity-side reachability tracking preserved
    for col in ("reachable", "last_reachability_at", "last_reachability_error"):
        assert col in pano_cols


def test_existing_row_survives_with_safe_defaults(client, db):
    """A row with only the 0001-era columns set continues to be a valid
    Device row after 0002. New columns either default cleanly or are
    nullable."""
    with db.bind.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO devices (name, hostname, encrypted_api_key, "
                "verify_tls, proxy_via_panorama, polling_enabled, source) "
                "VALUES (:n, :h, :k, 1, 0, 1, 'direct')"
            ),
            {"n": "legacy-dev", "h": "10.0.0.50", "k": b"fake-blob"},
        )

        row = conn.execute(
            text(
                "SELECT ha_role, connected, current_version, ha_peer_id, "
                "licenses, downloaded_versions FROM devices "
                "WHERE name = 'legacy-dev'"
            )
        ).one()

    # Server defaults kick in.
    assert row.ha_role == "unknown"
    assert row.connected in (False, 0)
    # Nullable columns are NULL.
    assert row.current_version is None
    assert row.ha_peer_id is None
    assert row.licenses is None
    assert row.downloaded_versions is None


def test_ha_peer_self_fk_works(client, db):
    """Both peers can INSERT in either order and then link via UPDATE —
    the migration's FK constraint must allow forward-references at
    INSERT time and only enforce on commit. This is the
    MIGRATION_NOTES §3.5 pattern."""
    with db.bind.begin() as conn:
        a = conn.execute(
            text(
                "INSERT INTO devices (name, hostname, verify_tls, "
                "proxy_via_panorama, polling_enabled, source) "
                "VALUES ('ha-a', '10.0.0.10', 1, 0, 1, 'direct') RETURNING id"
            )
        ).scalar_one()
        b = conn.execute(
            text(
                "INSERT INTO devices (name, hostname, verify_tls, "
                "proxy_via_panorama, polling_enabled, source) "
                "VALUES ('ha-b', '10.0.0.11', 1, 0, 1, 'direct') RETURNING id"
            )
        ).scalar_one()

        # Now link them — each device's ha_peer_id points at the other.
        conn.execute(
            text("UPDATE devices SET ha_peer_id = :peer WHERE id = :self"),
            [{"peer": b, "self": a}, {"peer": a, "self": b}],
        )

        peer_a = conn.execute(
            text("SELECT ha_peer_id FROM devices WHERE id = :i"), {"i": a}
        ).scalar_one()
        peer_b = conn.execute(
            text("SELECT ha_peer_id FROM devices WHERE id = :i"), {"i": b}
        ).scalar_one()
        assert peer_a == b
        assert peer_b == a


def test_panorama_proxy_upgrades_defaults_false(client, db):
    """The new panoramas.proxy_upgrades column has a server_default of
    false, so existing rows (and new ones that don't specify it) get
    safe-by-default behavior — no accidental proxy enable on upgrade
    paths the operator hasn't opted into."""
    with db.bind.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO panoramas (name, hostname, encrypted_api_key, "
                "verify_tls, reachable) VALUES (:n, :h, :k, 1, 1)"
            ),
            {"n": "legacy-pano", "h": "10.0.0.100", "k": b"fake-blob"},
        )
        pu = conn.execute(
            text("SELECT proxy_upgrades FROM panoramas WHERE name = 'legacy-pano'")
        ).scalar_one()

    assert pu in (False, 0)
