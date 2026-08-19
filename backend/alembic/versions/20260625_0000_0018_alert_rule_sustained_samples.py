"""alert_rules.sustained_samples + seed CPU/memory rules

Resource metrics (dp_cpu, mp_cpu, mp_memory) are now treated as percentages, so
they get heat-map color + alert evaluation like every other metric. CPU is
volatile, so add `sustained_samples`: an alert OPENS only after the threshold is
breached for that many consecutive polls (1 = instantaneous, the existing
behavior and the right default for slow-moving config counts).

Seed per-metric rules so CPU/memory alert on sensible, sustained thresholds out
of the box (CPU warn 85 / crit 95, memory warn 80 / crit 90, 3 consecutive
polls), overriding the global 75/90 instantaneous defaults for those metrics.

`sustained_samples` is added as NOT NULL DEFAULT 1, so every existing rule
(the two global defaults + any operator rules) keeps instantaneous behavior.

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-25 00:00:00 UTC
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (metric, severity, threshold_pct) — all seeded with sustained_samples = 3.
_RESOURCE_RULES = [
    ("dp_cpu", "warning", 85),
    ("dp_cpu", "critical", 95),
    ("mp_cpu", "warning", 85),
    ("mp_cpu", "critical", 95),
    ("mp_memory", "warning", 80),
    ("mp_memory", "critical", 90),
]


def upgrade() -> None:
    # ADD COLUMN with a server default works natively on both SQLite and
    # Postgres (no batch needed) and backfills existing rows to 1.
    op.add_column(
        "alert_rules",
        sa.Column(
            "sustained_samples",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )

    # Seed per-metric resource rules. These override the global default at the
    # same severity (the evaluator prefers a metric-specific rule). 'warning' /
    # 'critical' are the alert_severity enum values (see migration 0006).
    bind = op.get_bind()
    for metric, severity, threshold in _RESOURCE_RULES:
        bind.execute(
            sa.text(
                "INSERT INTO alert_rules "
                "(name, metric, severity, threshold_pct, sustained_samples, enabled) "
                "VALUES (:name, :metric, :severity, :threshold, 3, true)"
            ),
            {
                "name": f"{metric} {severity}",
                "metric": metric,
                "severity": severity,
                "threshold": threshold,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM alert_rules "
            "WHERE metric IN ('dp_cpu', 'mp_cpu', 'mp_memory')"
        )
    )
    op.drop_column("alert_rules", "sustained_samples")
