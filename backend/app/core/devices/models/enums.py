"""Enums used across the device data model."""

import enum


class DeviceSource(str, enum.Enum):
    """Where a device came from — manual add vs. Panorama import."""

    DIRECT = "direct"
    PANORAMA = "panorama"


class HARole(str, enum.Enum):
    """High-availability role for upgrade orchestration.

    Distinct from `Device.ha_state` which is the raw Panorama string
    ("active", "passive", "tentative", "non-functional", "initial",
    "suspended", "active-secondary", ...). `ha_role` is the
    upgrade-orchestrator's view: which one of the pair does it treat as
    the upgrade-first target.

    Carry-forward from pan-fw-upgrader's data model. The upgrade
    orchestrator's `_drive_pair_task` logic in phase 4c reads this.
    """

    STANDALONE = "standalone"
    ACTIVE = "active"
    PASSIVE = "passive"
    UNKNOWN = "unknown"
