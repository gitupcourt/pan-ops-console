"""Panorama CRUD + sync + test-connection."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.panorama import Panorama
from app.schemas import (
    AuthFromApiKey,
    AuthFromUserpass,
    PanoramaCreate,
    PanoramaDevicePreview,
    PanoramaRead,
    PanoramaSyncRequest,
)
from app.models.device import Device
from app.services.auth import decrypt_key, encrypt_key, mint_key
from app.services.panorama_client import PanoramaClient
from app.services.panorama_sync import sync_panorama

router = APIRouter(prefix="/panoramas", tags=["panoramas"])


def _to_read(pano: Panorama) -> PanoramaRead:
    return PanoramaRead.model_validate(
        {
            "id": pano.id,
            "name": pano.name,
            "hostname": pano.hostname,
            "has_api_key": pano.encrypted_api_key is not None,
            "verify_tls": pano.verify_tls,
            "reachable": pano.reachable,
            "last_sync_at": pano.last_sync_at,
            "last_reachability_at": pano.last_reachability_at,
            "last_reachability_error": pano.last_reachability_error,
        }
    )


def _resolve_auth(
    auth: AuthFromApiKey | AuthFromUserpass | None,
    host: str,
    verify_tls: bool,
) -> bytes | None:
    """Turn an auth payload into encrypted-key bytes, or None if no auth supplied."""
    if auth is None:
        return None
    if isinstance(auth, AuthFromApiKey):
        return encrypt_key(auth.api_key)
    try:
        return mint_key(host, auth.username, auth.password, verify_tls=verify_tls)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"keygen failed: {exc}") from exc


@router.get("", response_model=list[PanoramaRead])
def list_panoramas(db: Session = Depends(get_db)):
    return [_to_read(p) for p in db.query(Panorama).order_by(Panorama.name).all()]


@router.post("", response_model=PanoramaRead, status_code=201)
def create_panorama(payload: PanoramaCreate, db: Session = Depends(get_db)):
    if payload.auth is None:
        raise HTTPException(status_code=400, detail="auth is required on create")
    encrypted = _resolve_auth(payload.auth, payload.hostname, payload.verify_tls)
    pano = Panorama(
        name=payload.name,
        hostname=payload.hostname,
        verify_tls=payload.verify_tls,
        encrypted_api_key=encrypted,
    )
    db.add(pano)
    db.commit()
    db.refresh(pano)
    return _to_read(pano)


@router.patch("/{pano_id}", response_model=PanoramaRead)
def update_panorama(pano_id: int, payload: PanoramaCreate, db: Session = Depends(get_db)):
    pano = db.get(Panorama, pano_id)
    if pano is None:
        raise HTTPException(status_code=404, detail="not found")
    pano.name = payload.name
    pano.hostname = payload.hostname
    pano.verify_tls = payload.verify_tls
    if payload.auth is not None:
        pano.encrypted_api_key = _resolve_auth(payload.auth, pano.hostname, pano.verify_tls)
    db.commit()
    db.refresh(pano)
    return _to_read(pano)


@router.post("/{pano_id}/test-connection")
def test_panorama(pano_id: int, db: Session = Depends(get_db)):
    pano = db.get(Panorama, pano_id)
    if pano is None:
        raise HTTPException(status_code=404, detail="not found")
    if not pano.encrypted_api_key:
        raise HTTPException(status_code=400, detail="no API key stored")
    api_key = decrypt_key(pano.encrypted_api_key)
    client = PanoramaClient(pano.hostname, api_key, verify_tls=pano.verify_tls)
    try:
        info = client.test_connection()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "info": info}


@router.get("/{pano_id}/preview-devices", response_model=list[PanoramaDevicePreview])
def preview_devices(pano_id: int, db: Session = Depends(get_db)):
    """Ask Panorama what devices it knows about, WITHOUT writing to the DB.

    Used by the UI to populate the import picker. Each row is flagged
    `already_imported` if we already have a Device with that serial — those
    rows default to checked in the UI so re-sync refreshes them.
    """
    pano = db.get(Panorama, pano_id)
    if pano is None:
        raise HTTPException(status_code=404, detail="not found")
    if not pano.encrypted_api_key:
        raise HTTPException(status_code=400, detail="no API key stored")

    api_key = decrypt_key(pano.encrypted_api_key)
    client = PanoramaClient(pano.hostname, api_key, verify_tls=pano.verify_tls)
    try:
        managed = client.list_managed_devices()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    serials = [m.serial for m in managed]
    have = {
        s for (s,) in db.query(Device.serial).filter(Device.serial.in_(serials)).all() if s
    }

    return [
        PanoramaDevicePreview(
            serial=m.serial,
            hostname=m.hostname,
            ip_address=m.ip_address,
            model=m.model,
            sw_version=m.sw_version,
            connected=m.connected,
            already_imported=m.serial in have,
        )
        for m in managed
    ]


@router.post("/{pano_id}/sync", response_model=PanoramaRead)
def sync(
    pano_id: int,
    payload: PanoramaSyncRequest | None = None,
    db: Session = Depends(get_db),
):
    """Import / refresh selected devices from Panorama.

    Body is optional. With no body or empty `serials`, imports everything.
    With `serials: [...]`, only the listed serials are upserted.
    """
    pano = db.get(Panorama, pano_id)
    if pano is None:
        raise HTTPException(status_code=404, detail="not found")
    try:
        sync_panorama(db, pano, serial_filter=(payload.serials if payload else None))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    db.refresh(pano)
    return _to_read(pano)


@router.delete("/{pano_id}", status_code=204)
def delete_panorama(pano_id: int, db: Session = Depends(get_db)):
    pano = db.get(Panorama, pano_id)
    if pano is None:
        raise HTTPException(status_code=404, detail="not found")
    db.delete(pano)
    db.commit()
