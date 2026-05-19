"""Enums used across the data model.

Kept deliberately compatible with pan-fw-upgrader's enums.py so merging the
two apps later is a name-collision exercise, not a rewrite.
"""

import enum


class AuthType(str, enum.Enum):
    API_KEY = "api_key"
    USERPASS = "userpass"


class CredentialScope(str, enum.Enum):
    DEVICE = "device"
    PANORAMA = "panorama"


class DeviceSource(str, enum.Enum):
    DIRECT = "direct"
    PANORAMA = "panorama"
