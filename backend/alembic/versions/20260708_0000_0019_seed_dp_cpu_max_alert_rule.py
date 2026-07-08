"""seed dp_cpu_max alert rule

`dp_cpu_max` (hottest single DP core) shipped without a per-metric alert rule, so
it fell through to the global default — warn 75 / crit 90 with sustained_samples
= 1 (instant-fire). A single core legitimately spikes to 75%+ for one poll while
the cross-core average (`dp_cpu`, warn 85 / crit 95 × 3) stays calm, so the
default would open a warning on the noisiest possible signal at the lowest
threshold.

Seed a metric-specific rule: slightly higher thresholds than the average (the
hottest core is spikier) plus the same 3-consecutive-poll gate the other CPU
metrics use, so one transient spike doesn't open an alert.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-08 00:00:00 UTC
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (metric, severity, threshold_pct) — seeded sustained_samples = 3, mirroring
# migration 0018's resource-rule seeding.
_RULES = [
    ("dp_cpu_max", "warning", 90),
    ("dp_cpu_max", "critical", 98),
]


def upgrade() -> None:
    bind = op.get_bind()
    for metric, severity, threshold in _RULES:
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
    op.get_bind().execute(
        sa.text("DELETE FROM alert_rules WHERE metric = 'dp_cpu_max'")
    )
