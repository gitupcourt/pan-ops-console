"""Safe firewall disk-space cleanup.

Surfaced on the precheck disk-space alert and the Inventory row action. The
disk-space precheck warns the operator; this gives them a one-click,
conservative way to actually reclaim space.

Operator-approved SAFE scope (deliberately narrow):

  1. Delete downloaded software images OUTSIDE the device's current feature
     train — e.g. a leftover ``10.2.0`` base still on disk while the device
     runs ``11.1.4``. Base images are the largest files on a firewall, so
     old-train images are the biggest, safest reclaim. The CURRENT train
     (including its base, which a within-train rollback/upgrade may need) is
     never touched, and PAN-OS itself refuses to delete the running version
     as a backstop.
Old-image deletion is the whole safe pass. We do NOT run ``debug software
disk-usage cleanup``: the bare form isn't accepted on current PAN-OS, and the
only documented working form (``cleanup deep ...``) purges current log files —
outside our safe scope.

Explicitly NOT in scope: deleting logs (``aggressive-cleaning`` / ``deep``),
core files, or anything that loses troubleshooting history. The UI links the
PAN KB for manual deep-cleaning when the safe pass isn't enough.

Everything routes through ``build_client_with_fallback`` so the proxy-by-
default policy applies, same as the rest of the upgrade module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.command_proxy.builder import build_client_with_fallback
from app.core.command_proxy.pan_client import feature_train
from app.core.devices.models.device import Device

log = logging.getLogger(__name__)


@dataclass
class DeletableImage:
    version: str
    size_kb: str | None
    release_type: str  # "Base" for the largest (old-train) images


@dataclass
class CleanupPlan:
    device_id: int
    device_name: str
    current_version: str | None
    disk_space: list[dict]
    deletable_images: list[DeletableImage]


@dataclass
class CleanupResult:
    device_id: int
    device_name: str
    deleted: list[str]
    failed: list[dict]  # [{"version", "error"}]
    standard_cleanup_ran: bool
    standard_cleanup_output: str
    disk_space_before: list[dict]
    disk_space_after: list[dict]


def _current_version(software: list[dict], device: Device) -> str | None:
    """The running version per the live software list, falling back to the
    DB's recorded current_version. The live list is freshest."""
    live = next((i["version"] for i in software if i.get("current")), None)
    return live or device.current_version


def _deletable_images(software: list[dict], current_version: str | None) -> list[DeletableImage]:
    """The safe deletion set: downloaded, non-running images whose feature
    train differs from the device's current train.

    If we can't parse the current train we return NOTHING — refusing to guess
    is the safe failure mode (never risk deleting a current-train image a
    rollback might need).
    """
    cur_train = feature_train(current_version)
    if cur_train is None:
        return []
    out: list[DeletableImage] = []
    for img in software:
        ver = img.get("version")
        ft = feature_train(ver)
        if (
            img.get("downloaded")
            and not img.get("current")
            and ft is not None
            and ft != cur_train
        ):
            out.append(
                DeletableImage(
                    version=ver,
                    size_kb=img.get("size_kb"),
                    release_type=(img.get("release_type") or "").strip(),
                )
            )
    return out


def plan_cleanup(db: Session, device: Device) -> CleanupPlan:
    """Dry-run: report current disk usage + the images we WOULD delete.

    Pure reads (software list + disk-space). Nothing is deleted here — the UI
    shows this for the operator to confirm before any destructive call.
    """
    client, _route = build_client_with_fallback(db, device)
    software = client.list_software()
    current = _current_version(software, device)
    disk_space = client.get_disk_space()
    deletable = _deletable_images(software, current)
    log.info(
        "Disk-cleanup plan for %s: current=%s, %d deletable old-train image(s)",
        device.name, current, len(deletable),
    )
    return CleanupPlan(
        device_id=device.id,
        device_name=device.name,
        current_version=current,
        disk_space=disk_space,
        deletable_images=deletable,
    )


def execute_cleanup(db: Session, device: Device, versions: list[str]) -> CleanupResult:
    """Delete the requested images (intersected with the freshly-recomputed
    safe set). Measures disk space before/after.

    SECURITY: we never trust the caller's `versions` blindly — we recompute
    the safe set server-side from the live software list and refuse anything
    not in it. So even a tampered request can't delete the running version or
    a current-train image.
    """
    client, _route = build_client_with_fallback(db, device)
    software = client.list_software()
    current = _current_version(software, device)
    safe = {d.version for d in _deletable_images(software, current)}

    before = client.get_disk_space()
    deleted: list[str] = []
    failed: list[dict] = []
    for v in versions:
        if v not in safe:
            failed.append({
                "version": v,
                "error": "refused: not in the safe set (running/current-train images are never deleted)",
            })
            continue
        try:
            client.delete_software_image(v)
            deleted.append(v)
        except Exception as exc:  # noqa: BLE001 — surface per-image, keep going
            failed.append({"version": v, "error": str(exc)[:300]})

    # We intentionally DON'T run `debug software disk-usage cleanup` here: the
    # bare form isn't accepted on current PAN-OS (it errored on every device),
    # and the only documented working form (`cleanup deep ...`) purges current
    # log files — outside our safe scope. Old-image deletion is the safe,
    # high-value reclaim; the UI links the KB for manual deeper cleaning.
    # Fields kept (always false/empty) for API + frontend stability.
    std_ran = False
    std_out = ""

    after = client.get_disk_space()
    log.info(
        "Disk-cleanup on %s: deleted=%s failed=%d std_cleanup=%s",
        device.name, deleted, len(failed), std_ran,
    )
    return CleanupResult(
        device_id=device.id,
        device_name=device.name,
        deleted=deleted,
        failed=failed,
        standard_cleanup_ran=std_ran,
        standard_cleanup_output=std_out,
        disk_space_before=before,
        disk_space_after=after,
    )
