"""Device + shared credential-payload schemas.

`AuthFromUserpass` and `AuthFromApiKey` live here even though Panorama
creation also uses them — both rows store credentials the same way, and
keeping a single definition avoids isinstance() drift between two
identically-shaped classes.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.devices.models.enums import DeviceSource, HARole
from app.core.schema_utils import ensure_utc


# ---------- Credential payloads (used inline in Device/Panorama create/update) ----------

class AuthFromUserpass(BaseModel):
    mode: Literal["userpass"] = "userpass"
    username: str
    password: str


class AuthFromApiKey(BaseModel):
    mode: Literal["api_key"] = "api_key"
    api_key: str


# ---------- Devices ----------

class DeviceCreate(BaseModel):
    name: str
    hostname: str
    ip_address: str | None = None
    panorama_id: int | None = None
    verify_tls: bool = True
    proxy_via_panorama: bool = False
    polling_enabled: bool = True
    # Required on create when proxy_via_panorama=false. Optional on PATCH.
    auth: AuthFromUserpass | AuthFromApiKey | None = Field(default=None, discriminator="mode")


class DeviceRead(BaseModel):
    id: int
    name: str
    hostname: str
    ip_address: str | None
    serial: str | None
    model: str | None
    sw_version: str | None
    source: DeviceSource
    panorama_id: int | None
    has_api_key: bool
    verify_tls: bool
    proxy_via_panorama: bool
    polling_enabled: bool
    last_poll_at: datetime | None
    last_poll_error: str | None

    # Connection-state fields. Authoritative for Panorama-imported devices
    # (refreshed each Panorama sync); for direct-added devices these are
    # less meaningful since we don't have an external source to update
    # them (`connected` stays at False, `last_seen_at` reflects only what
    # the future poller-side write decides to do). UI should not show a
    # "disconnected" badge for direct devices on the strength of
    # `connected=False` alone — gate on `source == "panorama"`.
    connected: bool
    last_seen_at: datetime | None
    last_refresh_at: datetime | None
    ha_role: HARole
    ha_state: str | None

    model_config = {"from_attributes": True}

    @field_validator("last_poll_at", "last_seen_at", "last_refresh_at", mode="before")
    @classmethod
    def _utc(cls, v):
        return ensure_utc(v)
