"""RMA serial replacement.

When a firewall is RMA'd / factory-reset it comes back with a *new* serial,
while the operator still thinks of it as the same logical device. Panorama
sync, which dedupes by serial, auto-imports the new serial as a brand-new
record — leaving the original record (with all its history) stranded.

`replace_serial` re-points the original record to the new serial so the one
record carries forward across the hardware swap:

  - Identity + config stay (name, hostname, IP, Panorama link, proxy/TLS/
    polling, device-group / template-stack).
  - History stays — capacity samples, snapshots, and upgrade history are
    FK'd to this record's id, which doesn't change.
  - The new box's freshly-synced runtime/hardware state is carried over from
    the auto-imported duplicate, whose post-import child rows are re-pointed
    here (so they survive instead of cascade-deleting), and the duplicate is
    removed. Because the kept record now owns the new serial, the next sync
    dedupes onto it rather than re-creating the duplicate.
  - The stored API key is cleared — the replacement hardware needs fresh
    auth.

The caller owns the transaction: this function mutates + flushes but does not
commit, so the route can map errors to HTTP status codes.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.devices.models.device import Device

log = logging.getLogger(__name__)


class SerialReplaceError(ValueError):
    """Invalid replace request — surfaced by the route as a 400."""


# Runtime/hardware fields carried from the absorbed (new-hardware) record onto
# the record we keep. Identity, config, and history stay put; these reflect the
# physical box. Anything not listed refreshes on the next Panorama sync / poll,
# which now matches the kept record by its new serial.
_CARRY_FIELDS: tuple[str, ...] = (
    "model",
    "sw_version",
    "current_version",
    "ha_role",
    "ha_state",
    "ha_sync_state",
    "connected",
    "uptime",
    "staged_version",
    "staged_at",
    "staged_error",
    "downloaded_versions",
    "licenses",
    "app_version",
    "threat_version",
    "av_version",
    "wildfire_version",
    "url_filtering_version",
    "gp_client_version",
    "last_seen_at",
    "last_refresh_at",
)

# Tables whose `device_id` FK we re-point from the absorbed record to the kept
# record, so its post-import rows survive the merge instead of cascade-deleting
# with it. Fixed allowlist (never user input) — safe to interpolate.
_CHILD_TABLES: tuple[str, ...] = (
    "samples",
    "snapshots",
    "precheck_runs",
    "device_stage_runs",
    "alerts",
    "device_upgrade_tasks",
)


def replace_serial(db: Session, device: Device, new_serial: str) -> int | None:
    """Re-point `device` to `new_serial`, absorbing any duplicate.

    Returns the id of the absorbed duplicate (or None if the new serial wasn't
    already in inventory). Does not commit — the caller does.
    """
    new_serial = (new_serial or "").strip()
    if not new_serial:
        raise SerialReplaceError("A new serial is required.")
    if new_serial == device.serial:
        raise SerialReplaceError("Device already has this serial.")

    dup = db.query(Device).filter(Device.serial == new_serial).one_or_none()
    absorbed_id = dup.id if dup is not None else None

    if dup is not None:
        # Carry the replacement hardware's freshly-synced runtime state over.
        for field in _CARRY_FIELDS:
            setattr(device, field, getattr(dup, field))
        # Move the duplicate's post-import history onto the kept record.
        for tbl in _CHILD_TABLES:
            db.execute(
                text(f"UPDATE {tbl} SET device_id = :keep WHERE device_id = :drop"),  # noqa: S608
                {"keep": device.id, "drop": dup.id},
            )
        # Re-point any HA-peer pointer aimed at the duplicate.
        db.execute(
            text("UPDATE devices SET ha_peer_id = :keep WHERE ha_peer_id = :drop"),
            {"keep": device.id, "drop": dup.id},
        )
        db.delete(dup)
        # Free the unique serial before the kept record claims it.
        db.flush()

    device.serial = new_serial
    # The replacement box needs fresh auth — the old key won't authenticate.
    device.encrypted_api_key = None
    device.last_poll_error = None

    log.info(
        "RMA replace: device id=%s adopted serial=%s (absorbed dup id=%s)",
        device.id,
        new_serial,
        absorbed_id,
    )
    return absorbed_id
