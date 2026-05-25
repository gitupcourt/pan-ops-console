"""fix: panorama-imported devices' proxy_via_panorama default

Regression repair. The `panorama_sync` code on main (pre-fix) created
Device rows without setting `proxy_via_panorama`, leaving them at the
column's default of False. This caused Test/Capacity/Poll on
Panorama-imported devices to fail with "device has no API key" — the
device row had no inline API key (the proxy path means it doesn't
need one), but the route logic fell into the direct-credential branch
because `proxy_via_panorama=False`.

The code-path fix (sync now sets `proxy_via_panorama=True` on new
imports) takes effect for FUTURE syncs only. This migration repairs
the existing data in DBs that already imported under the buggy
default.

Filter criteria — match only rows that look like "imported by the
buggy code, no operator override":

  - source = 'panorama'           (came in via sync, not direct add)
  - encrypted_api_key IS NULL     (no key was set; sync doesn't mint
                                   one, and an operator who deliberately
                                   wanted direct would have pasted one)
  - proxy_via_panorama = false    (idempotent: already-True rows skip)

Won't run on freshly-installed environments (no rows match). Won't
clobber operator intent (matching criteria require all three).

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-25 23:00:00 UTC
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The source column is an enum on Postgres / a string on SQLite, but
    # the comparison against the literal 'panorama' works for both
    # because we've aligned Python enum .value with the Postgres ENUM
    # member name (see pan-ops-console#41 / #42 — values_callable fix).
    op.execute(
        """
        UPDATE devices
        SET proxy_via_panorama = true
        WHERE source = 'panorama'
          AND encrypted_api_key IS NULL
          AND proxy_via_panorama = false
        """
    )


def downgrade() -> None:
    # Intentionally a no-op. Reverting would re-introduce the
    # regression. Operators who want a specific device on the direct
    # path can PATCH it via the API.
    pass
