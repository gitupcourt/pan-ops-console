"""add upgrader columns to devices and panoramas

Phase 4a of the merge. Additive only — every existing capacity row stays
valid; the new columns are nullable or have a sensible default.

Adds to `devices`:
- current_version            (alongside transitional sw_version)
- HA: ha_peer_id (self-FK), ha_role enum, ha_state, ha_sync_state
- Panorama filters: device_group, template_stack
- Runtime: connected (Bool default false), uptime
- Staging: staged_version, staged_at, staged_error, downloaded_versions
- Snapshots: licenses (JSON), app/threat/av/wildfire/url_filtering/gp_client versions
- Timestamps: last_seen_at, last_refresh_at

Adds to `panoramas`:
- proxy_upgrades (Bool default false)

Does NOT touch:
- credentials storage (inline encrypted_api_key stays; the credentials-
  table-vs-inline reconciliation is deferred to phase 4c, see CLAUDE.md)
- Any capacity-side column (polling_enabled, last_poll_at, last_poll_error,
  encrypted_api_key, sw_version — all preserved as-is)

Capacity polling continues unchanged: it reads/writes its existing
columns, ignores the new ones. Phase 4c arrives with code that reads
the new columns; until then they're inert metadata.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-25 01:00:00 UTC
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------- panoramas ----------
    with op.batch_alter_table("panoramas") as batch:
        batch.add_column(
            sa.Column(
                "proxy_upgrades",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    # ---------- devices ----------
    # `ha_role` is a new enum type on Postgres. Unlike CREATE TABLE,
    # ALTER TABLE ADD COLUMN does NOT auto-create the underlying type —
    # we need to CREATE TYPE first, then reference it from the column.
    # The same Enum object set to create_type=False prevents the column-
    # level add_column from trying to create it a second time.
    # On SQLite there's no native enum (emulated as VARCHAR+CHECK); the
    # explicit create() is a no-op there.
    ha_role_enum = sa.Enum(
        "standalone", "active", "passive", "unknown",
        name="ha_role", create_type=False,
    )
    sa.Enum(
        "standalone", "active", "passive", "unknown", name="ha_role",
    ).create(op.get_bind(), checkfirst=True)

    with op.batch_alter_table("devices") as batch:
        # Version tracking
        batch.add_column(sa.Column("current_version", sa.String(length=64), nullable=True))

        # HA pairing
        batch.add_column(sa.Column("ha_peer_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "ha_role",
                ha_role_enum,
                nullable=False,
                server_default="unknown",
            )
        )
        batch.add_column(sa.Column("ha_state", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("ha_sync_state", sa.String(length=32), nullable=True))

        # Panorama-side metadata
        batch.add_column(sa.Column("device_group", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("template_stack", sa.String(length=255), nullable=True))

        # Runtime / connection state
        batch.add_column(
            sa.Column(
                "connected",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("uptime", sa.String(length=64), nullable=True))

        # Staging
        batch.add_column(sa.Column("staged_version", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("staged_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("staged_error", sa.String(length=2000), nullable=True))
        batch.add_column(sa.Column("downloaded_versions", sa.JSON(), nullable=True))

        # License + content snapshots
        batch.add_column(sa.Column("licenses", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("app_version", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("threat_version", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("av_version", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("wildfire_version", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("url_filtering_version", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("gp_client_version", sa.String(length=64), nullable=True))

        # Upgrader-side timestamps
        batch.add_column(
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True)
        )

        # Self-referential FK for HA pairing. `post_update=True` on the
        # SQLAlchemy relationship lets both peers INSERT before linking
        # without circular-FK violations on commit. Per MIGRATION_NOTES
        # §3.5: preserve this exact pattern or HA pair creation breaks.
        batch.create_foreign_key(
            "fk_devices_ha_peer_id",
            "devices",
            ["ha_peer_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_constraint("fk_devices_ha_peer_id", type_="foreignkey")

        batch.drop_column("last_refresh_at")
        batch.drop_column("last_seen_at")

        batch.drop_column("gp_client_version")
        batch.drop_column("url_filtering_version")
        batch.drop_column("wildfire_version")
        batch.drop_column("av_version")
        batch.drop_column("threat_version")
        batch.drop_column("app_version")
        batch.drop_column("licenses")

        batch.drop_column("downloaded_versions")
        batch.drop_column("staged_error")
        batch.drop_column("staged_at")
        batch.drop_column("staged_version")

        batch.drop_column("uptime")
        batch.drop_column("connected")

        batch.drop_column("template_stack")
        batch.drop_column("device_group")

        batch.drop_column("ha_sync_state")
        batch.drop_column("ha_state")
        batch.drop_column("ha_role")
        batch.drop_column("ha_peer_id")

        batch.drop_column("current_version")

    # Drop the Postgres-native ha_role enum type (no-op on SQLite).
    sa.Enum(name="ha_role").drop(op.get_bind(), checkfirst=True)

    with op.batch_alter_table("panoramas") as batch:
        batch.drop_column("proxy_upgrades")
