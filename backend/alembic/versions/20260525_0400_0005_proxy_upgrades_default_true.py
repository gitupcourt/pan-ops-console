"""policy: Panorama.proxy_upgrades defaults to True

Codifies the operator-side directive captured in CLAUDE.md ("default
to Panorama proxied even for the upgrader function; the operator has
the option to disable"). Companion change to migration 0004 which
flipped the device-side `proxy_via_panorama` default in the same
direction.

The `proxy_upgrades` flag is the panorama-level half of the two-layer
gate from MIGRATION_NOTES §14: for upgrade orchestration to actually
proxy a device, both Panorama.proxy_upgrades AND Device.proxy_via_panorama
must be True. With this migration, both layers default to True — opt-out
rather than opt-in.

The flag is currently UI-visible / API-configurable on the upgrader
side and **not yet read** by any runtime code in the merged app. This
migration is forward-looking: when phase 4 upgrade routes / orchestrator
wire-in starts reading the flag, the default value matches the policy.

## What this does

1. Updates any existing rows from `false` to `true`. Rows already at
   `true` are unaffected. Idempotent.
2. Alters the column's server_default so freshly INSERTed rows
   (e.g. an operator adding a new Panorama via the UI) get `true`.

## Operator override

After this lands, an operator who wants a particular Panorama to NOT
proxy upgrades can PATCH `proxy_upgrades=false` on that row via the
admin UI (once phase 4e exposes that toggle) or via a direct API call.
Per-device override remains `Device.proxy_via_panorama=false` on the
specific device(s).

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-25 23:30:00 UTC
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Update existing rows from False to True. Idempotent — already-True
    #    rows skip the WHERE clause.
    op.execute(
        "UPDATE panoramas SET proxy_upgrades = true WHERE proxy_upgrades = false"
    )

    # 2. Change the column's server_default so new rows default to True.
    #    Postgres ALTER COLUMN SET DEFAULT; SQLite handles this via batch
    #    mode (which env.py enables when on SQLite).
    with op.batch_alter_table("panoramas") as batch:
        batch.alter_column(
            "proxy_upgrades",
            server_default=sa.true(),
            existing_type=sa.Boolean(),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Revert the default. We do NOT mass-flip existing rows back to
    # false on downgrade — that would erase operator overrides and the
    # whole policy intent of this migration. Operators rolling back the
    # code can leave the data ahead of the schema; the runtime treats
    # both states correctly.
    with op.batch_alter_table("panoramas") as batch:
        batch.alter_column(
            "proxy_upgrades",
            server_default=sa.false(),
            existing_type=sa.Boolean(),
            existing_nullable=False,
        )
