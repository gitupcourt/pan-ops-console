"""Panorama CRUD, connection test, and device discovery."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_admin
from app.db import get_db
from app.models.credential import Credential
from app.models.device import Device
from app.models.panorama import Panorama
from app.models.user import User
from app.schemas.device import DeviceOut
from app.services.credentials import resolve as resolve_credential
from app.services.panorama_client import PanoramaClient, keygen
from app.services.panorama_sync import sync_all, sync_panorama

router = APIRouter(prefix="/api/panoramas", tags=["panoramas"])
log = logging.getLogger(__name__)


class PanoramaIn(BaseModel):
    name: str
    hostname: str
    credential_id: int
    proxy_upgrades: bool = False
    verify_tls: bool = True


class PanoramaOut(BaseModel):
    id: int
    name: str
    hostname: str
    credential_id: int
    proxy_upgrades: bool
    verify_tls: bool
    last_sync_at: datetime | None
    reachable: bool
    last_reachability_at: datetime | None
    last_reachability_error: str | None

    class Config:
        from_attributes = True


class KeygenIn(BaseModel):
    hostname: str
    username: str
    password: str
    verify_tls: bool = True


class KeygenOut(BaseModel):
    api_key: str


@router.post("/keygen", response_model=KeygenOut)
def panorama_keygen(payload: KeygenIn, _admin: User = Depends(require_admin)) -> KeygenOut:
    """Exchange username+password for a long-lived API key.

    The user can also generate one manually:
    https://docs.paloaltonetworks.com/pan-os/11-1/pan-os-panorama-api/get-started-with-the-pan-os-xml-api/get-your-api-key
    """
    try:
        key = keygen(
            payload.hostname, payload.username, payload.password, verify_tls=payload.verify_tls
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Keygen failed: {exc}") from exc
    return KeygenOut(api_key=key)


@router.get("", response_model=list[PanoramaOut])
def list_panoramas(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[Panorama]:
    return db.query(Panorama).order_by(Panorama.name).all()


@router.post("", response_model=PanoramaOut, status_code=201)
def add_panorama(
    payload: PanoramaIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> Panorama:
    if not db.get(Credential, payload.credential_id):
        raise HTTPException(400, f"Credential {payload.credential_id} not found")
    pano = Panorama(**payload.model_dump())
    db.add(pano)
    db.commit()
    db.refresh(pano)
    return pano


@router.delete("/{panorama_id}", status_code=204)
def delete_panorama(
    panorama_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    pano = db.get(Panorama, panorama_id)
    if not pano:
        raise HTTPException(404, "Panorama not found")
    db.delete(pano)
    db.commit()


@router.post("/{panorama_id}/test-connection")
def test_connection(
    panorama_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    pano = db.get(Panorama, panorama_id)
    if not pano:
        raise HTTPException(404, "Panorama not found")
    cred = resolve_credential(pano.credential)

    client = PanoramaClient(pano.hostname, cred, verify_tls=pano.verify_tls)
    try:
        info = client.test_connection()
    except Exception as exc:  # noqa: BLE001
        # Record the failure so the UI shows the banner / red badge.
        pano.reachable = False
        pano.last_reachability_at = datetime.now(timezone.utc)
        pano.last_reachability_error = str(exc)[:500]
        db.commit()
        raise HTTPException(502, f"Connection failed: {exc}") from exc

    # Mark reachable on success.
    pano.reachable = True
    pano.last_reachability_at = datetime.now(timezone.utc)
    pano.last_reachability_error = None
    db.commit()
    return info


@router.post("/{panorama_id}/import", response_model=list[DeviceOut])
def import_devices(
    panorama_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[Device]:
    """Pull the device list from Panorama and upsert into Device rows. Idempotent."""
    pano = db.get(Panorama, panorama_id)
    if not pano:
        raise HTTPException(404, "Panorama not found")
    try:
        return sync_panorama(db, pano)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Discovery failed: {exc}") from exc


@router.post("/refresh-all")
def refresh_all_panoramas(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Refresh devices from every configured Panorama. Returns per-Panorama counts."""
    return {"results": sync_all(db)}


@router.get("/{panorama_id}/device-groups")
def list_device_groups(
    panorama_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List unique device-group names already imported from this Panorama (cheap)."""
    pano = db.get(Panorama, panorama_id)
    if not pano:
        raise HTTPException(404, "Panorama not found")
    rows = (
        db.query(Device.device_group)
        .filter(Device.panorama_id == panorama_id, Device.device_group.isnot(None))
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows})


@router.get("/{panorama_id}/template-stacks")
def list_template_stacks(
    panorama_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    pano = db.get(Panorama, panorama_id)
    if not pano:
        raise HTTPException(404, "Panorama not found")
    rows = (
        db.query(Device.template_stack)
        .filter(Device.panorama_id == panorama_id, Device.template_stack.isnot(None))
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows})
