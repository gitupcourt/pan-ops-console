"""device_upgrade_tasks.device_id ON DELETE CASCADE

`device_upgrade_tasks.device_id` was created NO ACTION, while every other
device-scoped child table (snapshots, precheck_runs, device_stage_runs,
alerts, capacity samples) is ON DELETE CASCADE. That inconsistency meant a
device that had ever been part of an upgrade job could not be deleted: the
DELETE hit a foreign-key violation on its leftover task rows, the API
returned a 500, and (because the UI swallowed it) the operator just saw the
delete button "do nothing". This was hit in the field deleting a
factory-reset device whose old serial lingered in inventory.

Bring the constraint in line with its siblings so deleting a device removes
its upgrade-task history with it. The parent UpgradeJob row and other
devices' tasks in the same job are untouched (they don't reference the
deleted device).

batch_alter_table + an explicit naming convention so the drop/recreate works
on SQLite too: SQLite has no native ALTER ... DROP CONSTRAINT, so alembic
copy-rebuilds the table, and the convention lets it match the existing
`fk_device_upgrade_tasks_device_id` constraint during the rebuild. On
Postgres the same calls issue a native DROP/ADD CONSTRAINT (no table copy).

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-16 00:00:00 UTC
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The metadata carries no global naming convention (constraints are named
# explicitly per-migration), so hand batch mode the convention it needs to
# identify/recreate the FK on SQLite.
_NAMING_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s"}
_FK = "fk_device_upgrade_tasks_device_id"


def upgrade() -> None:
    with op.batch_alter_table(
        "device_upgrade_tasks", naming_convention=_NAMING_CONVENTION
    ) as batch:
        batch.drop_constraint(_FK, type_="foreignkey")
        batch.create_foreign_key(
            _FK, "devices", ["device_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    with op.batch_alter_table(
        "device_upgrade_tasks", naming_convention=_NAMING_CONVENTION
    ) as batch:
        batch.drop_constraint(_FK, type_="foreignkey")
        batch.create_foreign_key(_FK, "devices", ["device_id"], ["id"])
