"""precheck_sets: seed defaults + add upgrade_jobs.precheck_set_id

Phase 13b — wires the existing `precheck_sets` table into the upgrade
job flow. The table itself was created in migration 0003 (phase
4c-models) but has been dormant; this migration:

  1. Seeds two default `PrecheckSet` rows ("Standard", "Full") so
     operators have working presets the moment phase 13b lands.
  2. Adds `upgrade_jobs.precheck_set_id` (nullable FK) so a job can
     reference which set to run. NULL = "use the default set"; the
     orchestrator falls back to whichever PrecheckSet has
     `is_default = true`, and if no such row exists, to the
     hard-coded DEFAULT_READINESS_CHECKS in pan_client.py.

Hardcoded check name lists below mirror what's in
`app.core.command_proxy.pan_client.DEFAULT_READINESS_CHECKS` /
`ALL_READINESS_CHECKS` at the time of writing. They're snapshotted
here on purpose — the catalog of available checks can grow without
this migration's seeded sets changing, and operators can edit the
sets after the fact via the CRUD API.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-27 00:00:00 UTC
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Snapshot of DEFAULT_READINESS_CHECKS from pan_client.py at this
# revision. The "Standard" preset mirrors it. Changing the live
# constant later does not retroactively change this seeded row —
# that's intentional, the seed is a starting point operators tune.
_STANDARD_CHECKS: list[str] = [
    "ha",
    "panorama",
    "candidate_config",
    "free_disk_space",
    "expired_licenses",
    "ntp_sync",
    "content_version",
    "dynamic_updates",
    "jobs",
]

# Snapshot of ALL_READINESS_CHECKS at this revision. "Full" preset.
_FULL_CHECKS: list[str] = [
    "active_support",
    "arp_entry_exist",
    "candidate_config",
    "certificates_requirements",
    "content_version",
    "dynamic_updates",
    "expired_licenses",
    "free_disk_space",
    "global_jumbo_frame",
    "ha",
    "ip_sec_tunnel_status",
    "jobs",
    "mp_cpu_utilization",
    "mp_mem_utilization",
    "ntp_sync",
    "panorama",
    "planes_clock_sync",
    "session_exist",
    "environmentals",
]


def upgrade() -> None:
    # Add the FK column on upgrade_jobs. NULL is fine — orchestrator
    # has a fallback path that uses the system-default precheck set
    # (or DEFAULT_READINESS_CHECKS if no defaults exist).
    op.add_column(
        "upgrade_jobs",
        sa.Column(
            "precheck_set_id",
            sa.Integer(),
            sa.ForeignKey("precheck_sets.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_upgrade_jobs_precheck_set_id",
        "upgrade_jobs",
        ["precheck_set_id"],
    )

    # Seed the two presets. JSON-encoded for portability (works on
    # SQLite + Postgres without dialect tricks).
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO precheck_sets (name, description, checks, is_default) "
            "VALUES (:name, :description, :checks, :is_default)"
        ),
        [
            {
                "name": "Standard",
                "description": (
                    "Balanced default — covers the highest-signal "
                    "readiness checks without slowing the upgrade flow "
                    "on the long tail of edge-case checks."
                ),
                "checks": json.dumps(_STANDARD_CHECKS),
                "is_default": True,
            },
            {
                "name": "Full",
                "description": (
                    "Every available readiness check. Slowest but most "
                    "thorough; useful for safety-critical paths or when "
                    "investigating intermittent precheck failures."
                ),
                "checks": json.dumps(_FULL_CHECKS),
                "is_default": False,
            },
        ],
    )


def downgrade() -> None:
    # Wipe the seeded rows so a fresh upgrade re-seeds cleanly. We
    # match by name since that's the unique constraint.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM precheck_sets WHERE name IN ('Standard', 'Full')"
        )
    )

    op.drop_index("ix_upgrade_jobs_precheck_set_id", table_name="upgrade_jobs")
    op.drop_column("upgrade_jobs", "precheck_set_id")
