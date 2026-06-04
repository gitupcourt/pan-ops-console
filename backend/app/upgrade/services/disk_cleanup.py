"""Safe firewall disk-space cleanup.

Surfaced on the precheck disk-space alert and the Inventory row action. The
disk-space precheck warns the operator; this gives them a one-click,
conservative way to actually reclaim space.

Scope — delete downloaded software images OLDER than the running version: old
other-train images (e.g. a leftover ``10.2.0`` base while running ``12.1.7``)
AND old same-train maintenance releases (e.g. ``12.1.3`` while running
``12.1.7``). Base images are the largest files on a firewall, so these are the
biggest, safest reclaim. Always kept: the running version (PAN-OS refuses to
delete it anyway), the current train's base (a within-train install/rollback
needs it), and anything NEWER than running (likely pre-staged for an upcoming
upgrade).

NOT in scope (deliberately): rotated-log / temp "deep clean" and per-file
deletes. The `debug software disk-usage cleanup deep` command has fiddly
threshold semantics and reclaims little (logs rotate frequently), so it was
dropped — the UI links the PAN KB for manual deeper cleaning when needed.

Everything routes through ``build_client_with_fallback`` so the proxy-by-
default policy applies, same as the rest of the upgrade module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.command_proxy.builder import build_client_with_fallback
from app.core.command_proxy.pan_client import feature_train, version_tuple
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
    disk_space_before: list[dict]
    disk_space_after: list[dict]


def _current_version(software: list[dict], device: Device) -> str | None:
    """The running version per the live software list, falling back to the
    DB's recorded current_version. The live list is freshest."""
    live = next((i["version"] for i in software if i.get("current")), None)
    return live or device.current_version


def _deletable_images(software: list[dict], current_version: str | None) -> list[DeletableImage]:
    """The safe deletion set: downloaded images OLDER than the running version,
    keeping the running version and the CURRENT train's base.

    "Older than running" is the key safety rule. It reclaims:
      - old other-train images (e.g. 11.x / 10.x while running 12.1.7), and
      - old SAME-train maintenance releases (e.g. 12.1.3 while running 12.1.7) —
        downloaded-but-not-installed leftovers from past upgrades.
    It deliberately does NOT touch anything NEWER than running, since a newer
    downloaded image is almost always one the operator pre-staged for an
    upcoming upgrade — deleting it would undo that staging.

    Always kept: the running version (PAN-OS refuses to delete it anyway) and
    the current train's BASE image (a within-train install/rollback needs it).
    If we can't parse the running version we return NOTHING — refusing to guess
    is the safe failure mode.
    """
    cur_train = feature_train(current_version)
    if cur_train is None or not current_version:
        return []
    cur_tuple = version_tuple(current_version)
    out: list[DeletableImage] = []
    for img in software:
        ver = img.get("version")
        if not img.get("downloaded") or img.get("current"):
            continue
        # Keep the current train's base — needed for within-train work.
        if feature_train(ver) == cur_train and (img.get("release_type") or "").strip() == "Base":
            continue
        # Only OLDER than the running version (never a pre-staged newer image).
        if version_tuple(ver) >= cur_tuple:
            continue
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

    after = client.get_disk_space()
    log.info(
        "Disk-cleanup on %s: deleted=%s failed=%d",
        device.name, deleted, len(failed),
    )
    return CleanupResult(
        device_id=device.id,
        device_name=device.name,
        deleted=deleted,
        failed=failed,
        disk_space_before=before,
        disk_space_after=after,
    )
