"""Pydantic schemas for the API surface."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import AuthType, CredentialScope, DeviceSource


# ---------- Credentials ----------

class CredentialCreate(BaseModel):
    name: str
    description: str | None = None
    auth_type: AuthType
    scope: CredentialScope
    api_key: str | None = None
    username: str | None = None
    password: str | None = None


class CredentialRead(BaseModel):
    id: int
    name: str
    description: str | None
    auth_type: AuthType
    scope: CredentialScope
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------- Panoramas ----------

class PanoramaCreate(BaseModel):
    name: str
    hostname: str
    credential_id: int
    verify_tls: bool = True


class PanoramaRead(BaseModel):
    id: int
    name: str
    hostname: str
    credential_id: int
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
    credential_id: int | None = None
    panorama_id: int | None = None
    verify_tls: bool = True
    proxy_via_panorama: bool = False
    polling_enabled: bool = True


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
    credential_id: int | None
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
