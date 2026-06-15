"""Panorama schemas + device-import preview shapes."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.devices.schemas import AuthFromApiKey, AuthFromUserpass
from app.core.schema_utils import ensure_utc


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
    sw_version: str | None

    model_config = {"from_attributes": True}

    @field_validator("last_sync_at", "last_reachability_at", mode="before")
    @classmethod
    def _utc(cls, v):
        return ensure_utc(v)


class PanoramaDevicePreview(BaseModel):
    """One row from `show devices all` on Panorama, with a flag for whether
    we already have it in our DB. The UI uses these to populate the import
    picker.
    """
    serial: str
    hostname: str | None
    ip_address: str | None
    model: str | None
    sw_version: str | None
    connected: bool
    already_imported: bool


class PanoramaSyncRequest(BaseModel):
    """Filter sync to a specific set of serials. Empty list / missing = import all."""
    serials: list[str] | None = None
