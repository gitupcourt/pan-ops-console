from datetime import datetime
from pydantic import BaseModel

from app.models.enums import DeviceSource, HARole


class DeviceBase(BaseModel):
    name: str
    hostname: str
    ip_address: str | None = None
    serial: str | None = None
    ha_role: HARole = HARole.UNKNOWN
    ha_peer_id: int | None = None
    device_group: str | None = None
    template_stack: str | None = None


class DeviceCreate(DeviceBase):
    source: DeviceSource = DeviceSource.DIRECT
    panorama_id: int | None = None
    credential_id: int | None = None


class LatestPrecheck(BaseModel):
    id: int
    ran_at: datetime
    overall_severity: str
    pass_count: int
    warn_count: int
    fail_count: int
    skip_count: int


class DeviceOut(DeviceBase):
    id: int
    source: DeviceSource
    panorama_id: int | None
    credential_id: int | None
    verify_tls: bool
    proxy_via_panorama: bool
    current_version: str | None
    model: str | None
    connected: bool
    uptime: str | None
    ha_state: str | None
    ha_sync_state: str | None
    app_version: str | None
    threat_version: str | None
    av_version: str | None
    wildfire_version: str | None
    url_filtering_version: str | None
    gp_client_version: str | None
    created_at: datetime
    last_seen_at: datetime | None
    last_refresh_at: datetime | None
    staged_version: str | None
    staged_at: datetime | None
    staged_error: str | None
    downloaded_versions: list[str] | None
    # Most-recent precheck summary (denormalized so the UI doesn't need an extra
    # round-trip). Populated by the route from PrecheckRun.
    latest_precheck: LatestPrecheck | None = None

    class Config:
        from_attributes = True
