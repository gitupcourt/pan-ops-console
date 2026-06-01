"""Admin routes: runtime polling config + per-Panorama pressure (#89 PR-4).

`GET/PATCH /settings/polling` edit the dispatcher's cadences + per-Panorama
concurrency at runtime (the dispatcher reads them each tick — no redeploy).
`GET /settings/polling/pressure` surfaces per-Panorama in-flight polls (from
the Redis semaphore, best-effort) + device/error counts so an operator can
see whether a Panorama is saturated and tune accordingly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DBSession

from app.capacity.models.polling_config import PollingConfig
from app.capacity.schemas import (
    PanoramaPressure,
    PollingConfigRead,
    PollingConfigUpdate,
    PollingPressure,
)
from app.capacity.services.polling_config import get_polling_config
from app.config import get_settings
from app.core.auth.deps import current_admin
from app.core.auth.models.user import User
from app.db import get_db

router = APIRouter(prefix="/settings/polling", tags=["polling"])


def _read(cfg: PollingConfig) -> PollingConfigRead:
    return PollingConfigRead(
        system_interval_seconds=cfg.system_interval_seconds,
        config_interval_seconds=cfg.config_interval_seconds,
        max_concurrency_per_panorama=cfg.max_concurrency_per_panorama,
        device_retry_backoff_seconds=cfg.device_retry_backoff_seconds,
        dispatch_tick_seconds=get_settings().POLL_DISPATCH_TICK_SECONDS,
        updated_at=cfg.updated_at,
    )


@router.get("", response_model=PollingConfigRead)
def get_config(db: DBSession = Depends(get_db), _: User = Depends(current_admin)):
    return _read(get_polling_config(db))


@router.patch("", response_model=PollingConfigRead)
def update_config(
    payload: PollingConfigUpdate,
    db: DBSession = Depends(get_db),
    _: User = Depends(current_admin),
):
    row = db.get(PollingConfig, 1)
    if row is None:
        # Migration 0012 seeds this; recreate defensively if it's gone.
        s = get_settings()
        row = PollingConfig(
            id=1,
            system_interval_seconds=s.POLL_SYSTEM_INTERVAL_SECONDS or s.POLL_INTERVAL_SECONDS,
            config_interval_seconds=s.POLL_CONFIG_INTERVAL_SECONDS,
            max_concurrency_per_panorama=s.POLL_MAX_CONCURRENCY_PER_PANORAMA,
            device_retry_backoff_seconds=s.POLL_DEVICE_RETRY_BACKOFF_SECONDS,
        )
        db.add(row)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return _read(row)


@router.get("/pressure", response_model=PollingPressure)
def get_pressure(db: DBSession = Depends(get_db), _: User = Depends(current_admin)):
    from app.core import concurrency
    from app.core.devices.models.device import Device

    cap = get_polling_config(db).max_concurrency_per_panorama
    devices = db.query(Device).filter(Device.polling_enabled == True).all()  # noqa: E712

    groups: dict[str, dict] = {}
    for d in devices:
        key = concurrency.pano_key_for(d)
        g = groups.setdefault(key, {"label": None, "devices": 0, "errors": 0})
        g["devices"] += 1
        if d.last_poll_error:
            g["errors"] += 1
        if key != "direct" and g["label"] is None and d.panorama is not None:
            g["label"] = d.panorama.name

    out: list[PanoramaPressure] = []
    for key, g in sorted(groups.items()):
        try:
            in_flight = concurrency.inflight_count(key)
        except Exception:  # noqa: BLE001 — telemetry is best-effort (Redis may be unreachable)
            in_flight = None
        label = g["label"] or ("Direct-polled" if key == "direct" else f"Panorama {key}")
        out.append(
            PanoramaPressure(
                pano_key=key,
                label=label,
                in_flight=in_flight,
                cap=cap,
                devices=g["devices"],
                devices_in_error=g["errors"],
            )
        )
    return PollingPressure(per_panorama=out)
