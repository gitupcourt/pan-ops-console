"""initial schema baseline

Captures the schema as it existed at the end of phase 1 — User/Session/BackupCode,
OIDCProvider, Panorama, Device, Sample. The shape matches what
`Base.metadata.create_all` produced on every prior startup, so a dev or prod
DB that already exists upgrades to this revision with zero data movement
(the alembic_version table gets a row, nothing else).

Phase 4a will add additive ALTER TABLE migrations on devices + panoramas for
the upgrader module's columns (HA fields, version tracking, kind enum, etc.).

Revision ID: 0001
Revises:
Create Date: 2026-05-25 00:00:00 UTC
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------- users ----------
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("encrypted_totp_secret", sa.LargeBinary(), nullable=True),
        sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    # ---------- sessions ----------
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.String(length=64), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_sessions_user_id",
        ),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    # ---------- backup_codes ----------
    op.create_table(
        "backup_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_backup_codes_user_id",
        ),
        sa.UniqueConstraint("code_hash", name="uq_backup_codes_code_hash"),
    )
    op.create_index("ix_backup_codes_user_id", "backup_codes", ["user_id"])
    op.create_index("ix_backup_codes_code_hash", "backup_codes", ["code_hash"], unique=True)

    # ---------- oidc_providers ----------
    op.create_table(
        "oidc_providers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("issuer", sa.String(length=500), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("encrypted_client_secret", sa.LargeBinary(), nullable=False),
        sa.Column(
            "scopes",
            sa.String(length=500),
            nullable=False,
            server_default="openid email profile",
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("slug", name="uq_oidc_providers_slug"),
    )
    op.create_index("ix_oidc_providers_slug", "oidc_providers", ["slug"], unique=True)

    # ---------- panoramas ----------
    op.create_table(
        "panoramas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("encrypted_api_key", sa.LargeBinary(), nullable=True),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reachable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_reachability_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reachability_error", sa.String(length=500), nullable=True),
        sa.UniqueConstraint("name", name="uq_panoramas_name"),
    )

    # ---------- devices ----------
    # device_source enum — the column-level definition below auto-creates
    # the underlying type (CREATE TYPE on Postgres; CHECK constraint on
    # SQLite). No need for a separate explicit create — that pattern was
    # the source of a DuplicateObject error on Postgres because alembic
    # fires CREATE TYPE again when the Enum is referenced inside
    # create_table, even with create_type=False on the Enum.
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("serial", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("sw_version", sa.String(length=64), nullable=True),
        sa.Column(
            "source",
            sa.Enum("direct", "panorama", name="device_source"),
            nullable=False,
            server_default="direct",
        ),
        sa.Column("panorama_id", sa.Integer(), nullable=True),
        sa.Column("encrypted_api_key", sa.LargeBinary(), nullable=True),
        sa.Column("verify_tls", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "proxy_via_panorama",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "polling_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_poll_error", sa.String(length=2000), nullable=True),
        sa.ForeignKeyConstraint(
            ["panorama_id"],
            ["panoramas.id"],
            name="fk_devices_panorama_id",
        ),
        sa.UniqueConstraint("name", name="uq_devices_name"),
        sa.UniqueConstraint("serial", name="uq_devices_serial"),
    )

    # ---------- samples ----------
    op.create_table(
        "samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("current_value", sa.Float(), nullable=False),
        sa.Column("max_value", sa.Float(), nullable=True),
        sa.Column("pct", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            ondelete="CASCADE",
            name="fk_samples_device_id",
        ),
    )
    op.create_index("ix_samples_device_id", "samples", ["device_id"])
    op.create_index("ix_samples_metric", "samples", ["metric"])
    op.create_index("ix_samples_ts", "samples", ["ts"])
    op.create_index(
        "ix_samples_device_metric_ts", "samples", ["device_id", "metric", "ts"]
    )


def downgrade() -> None:
    op.drop_index("ix_samples_device_metric_ts", table_name="samples")
    op.drop_index("ix_samples_ts", table_name="samples")
    op.drop_index("ix_samples_metric", table_name="samples")
    op.drop_index("ix_samples_device_id", table_name="samples")
    op.drop_table("samples")

    op.drop_table("devices")
    # device_source enum: on Postgres, the type lingers after drop_table —
    # explicit drop here, no-op on SQLite (the dialect has no native enum).
    sa.Enum(name="device_source").drop(op.get_bind(), checkfirst=True)

    op.drop_table("panoramas")

    op.drop_index("ix_oidc_providers_slug", table_name="oidc_providers")
    op.drop_table("oidc_providers")

    op.drop_index("ix_backup_codes_code_hash", table_name="backup_codes")
    op.drop_index("ix_backup_codes_user_id", table_name="backup_codes")
    op.drop_table("backup_codes")

    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")

    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
