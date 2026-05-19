"""Pydantic schemas for the API surface.

Auth model: API keys live on the Device / Panorama row. Creating or editing
one of those rows takes an optional `auth` payload that says either "mint a
key now from username+password" or "here's an API key, store it." If `auth`
is omitted on edit, the existing stored key is kept.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import DeviceSource


# ---------- Auth payloads (used inline in Device/Panorama create/update) ----------

class AuthFromUserpass(BaseModel):
    mode: Literal["userpass"] = "userpass"
    username: str
    password: str


class AuthFromApiKey(BaseModel):
    mode: Literal["api_key"] = "api_key"
    api_key: str


# ---------- Panoramas ----------

class PanoramaCreate(BaseModel):
    name: str
    hostname: str
    verify_tls: bool = True
    # Required at create time. Optional on PATCH (omit to keep the existing key).
    auth: AuthFromUserpass | AuthFromApiKey | None = Field(default=None, discriminator="mode")


class PanoramaRead(BaseModel):
    id: int
    name: str
    hostname: str
    has_api_key: bool
    verify_tls: bool
    reachable: bool
    last_sync_at: datetime | None
    last_reachability_at: datetime | None
    last_reachability_error: str | None

    model_config = {"from_attributes": True}


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

    model_config = {"from_attributes": True}


# ---------- Samples ----------

class SampleRead(BaseModel):
    ts: datetime
    current: float = Field(alias="current_value")
    max: float | None = Field(default=None, alias="max_value")
    pct: float | None

    model_config = {"from_attributes": True, "populate_by_name": True}


class MetricSeries(BaseModel):
    device_id: int
    metric: str
    samples: list[SampleRead]
