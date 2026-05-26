"""alerts: AlertRule + Alert tables, seed default rules

Phase 12a — stands up the data model behind the rule engine that
fires on capacity threshold breaches. Two new tables:

  - `alert_rules` — operator-configurable thresholds. Seeded with
    two global defaults at the warning (75%) and critical (90%)
    bands.
  - `alerts` — fired alert state. One row per (device, metric)
    while open; closed rows stay as history (cleared_at non-null).

Defaults seeded inside this migration so a fresh install has
useful alerts immediately on the first poll cycle. Operators can
disable / edit / add more rules via the rules-management UI in
phase 12b.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-26 22:30:00 UTC
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # On Postgres, create the alert_severity ENUM up-front so multiple
    # CREATE TABLEs referencing it don't race to CREATE TYPE. On
    # SQLite the per-column Enum just emits a VARCHAR + CHECK.
    if bind.dialect.name == "postgresql":
        op.execute(
            "DO $$ BEGIN "
            "CREATE TYPE alert_severity AS ENUM ('warning', 'critical'); "
            "EXCEPTION WHEN duplicate_object THEN null; "
            "END $$;"
        )

    severity_col = sa.Enum(
        "warning",
        "critical",
        name="alert_severity",
        create_type=False,
    )

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=True),
        sa.Column("severity", severity_col, nullable=False),
        sa.Column("threshold_pct", sa.Integer(), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
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
        sa.UniqueConstraint(
            "metric", "severity", name="uq_alert_rules_metric_severity"
        ),
    )
    op.create_index("ix_alert_rules_metric", "alert_rules", ["metric"])
    op.create_index("ix_alert_rules_severity", "alert_rules", ["severity"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("severity", severity_col, nullable=False),
        sa.Column("threshold_pct", sa.Integer(), nullable=False),
        sa.Column("current_value", sa.Float(), nullable=False),
        sa.Column("max_value", sa.Float(), nullable=True),
        sa.Column("pct", sa.Float(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "acknowledged_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_alerts_device_id", "alerts", ["device_id"])
    op.create_index("ix_alerts_metric", "alerts", ["metric"])
    op.create_index("ix_alerts_severity", "alerts", ["severity"])
    op.create_index("ix_alerts_cleared_at", "alerts", ["cleared_at"])
    op.create_index("ix_alerts_first_seen_at", "alerts", ["first_seen_at"])
    # Composite index for the evaluator's hot lookup: "open alerts on
    # this device/metric." cleared_at trailing keeps it useful for the
    # WHERE cleared_at IS NULL filter via partial-index semantics on
    # PG / index-scan-with-filter on SQLite.
    op.create_index(
        "ix_alerts_device_metric_open",
        "alerts",
        ["device_id", "metric", "cleared_at"],
    )

    # Seed two global default rules so a fresh install starts firing
    # alerts on the first poll cycle without operator effort. These
    # are operator-editable like any other rule.
    op.execute(
        "INSERT INTO alert_rules (name, metric, severity, threshold_pct, enabled) "
        "VALUES ('Default Warning', NULL, 'warning', 75, true)"
    )
    op.execute(
        "INSERT INTO alert_rules (name, metric, severity, threshold_pct, enabled) "
        "VALUES ('Default Critical', NULL, 'critical', 90, true)"
    )


def downgrade() -> None:
    op.drop_index("ix_alerts_device_metric_open", table_name="alerts")
    op.drop_index("ix_alerts_first_seen_at", table_name="alerts")
    op.drop_index("ix_alerts_cleared_at", table_name="alerts")
    op.drop_index("ix_alerts_severity", table_name="alerts")
    op.drop_index("ix_alerts_metric", table_name="alerts")
    op.drop_index("ix_alerts_device_id", table_name="alerts")
    op.drop_table("alerts")

    op.drop_index("ix_alert_rules_severity", table_name="alert_rules")
    op.drop_index("ix_alert_rules_metric", table_name="alert_rules")
    op.drop_table("alert_rules")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS alert_severity")
