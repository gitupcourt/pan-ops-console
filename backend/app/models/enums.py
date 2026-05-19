"""Enums used across the data model."""

import enum


class DeviceSource(str, enum.Enum):
    """Where a device came from — manual add vs. Panorama import."""

    DIRECT = "direct"
    PANORAMA = "panorama"
