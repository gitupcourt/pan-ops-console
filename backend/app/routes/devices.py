"""Device CRUD + test-connection."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.device import Device
from app.schemas import AuthFromApiKey, AuthFromUserpass, DeviceCreate, DeviceRead
from app.services.auth import decrypt_key, encrypt_key, mint_key
from app.services.pan_client import PanDeviceClient

router = APIRouter(prefix="/devices", tags=["devices"])


def _to_read(d: Device) -> DeviceRead:
    return DeviceRead.model_validate(
        {
            "id": d.id,
            "name": d.name,
            "hostname": d.hostname,
            "ip_address": d.ip_address,
            "serial": d.serial,
            "model": d.model,
            "sw_version": d.sw_version,
            "source": d.source,
            "panorama_id": d.panorama_id,
            "has_api_key": d.encrypted_api_key is not None,
            "verify_tls": d.verify_tls,
            "proxy_via_panorama": d.proxy_via_panorama,
            "polling_enabled": d.polling_enabled,
            "last_poll_at": d.last_poll_at,
            "last_poll_error": d.last_poll_error,
        }
    )


def _resolve_auth(
    auth: AuthFromApiKey | AuthFromUserpass | None,
    host: str,
    verify_tls: bool,
) -> bytes | None:
    if auth is None:
        return None
    if isinstance(auth, AuthFromApiKey):
        return encrypt_key(auth.api_key)
    try:
        return mint_key(host, auth.username, auth.password, verify_tls=verify_tls)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"keygen failed: {exc}") from exc


@router.get("", response_model=list[DeviceRead])
def list_devices(db: Session = Depends(get_db)):
    return [_to_read(d) for d in db.query(Device).order_by(Device.name).all()]


@router.post("", response_model=DeviceRead, status_code=201)
def create_device(payload: DeviceCreate, db: Session = Depends(get_db)):
    if not payload.proxy_via_panorama and payload.auth is None:
        raise HTTPException(
            status_code=400,
            detail="auth is required for direct devices (or set proxy_via_panorama=true)",
        )
    if payload.proxy_via_panorama and payload.panorama_id is None:
        raise HTTPException(
            status_code=400,
            detail="proxy_via_panorama requires panorama_id",
        )

    target = payload.ip_address or payload.hostname
    encrypted = _resolve_auth(payload.auth, target, payload.verify_tls)

    device = Device(
        name=payload.name,
        hostname=payload.hostname,
        ip_address=payload.ip_address,
        panorama_id=payload.panorama_id,
        verify_tls=payload.verify_tls,
        proxy_via_panorama=payload.proxy_via_panorama,
        polling_enabled=payload.polling_enabled,
        encrypted_api_key=encrypted,
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return _to_read(device)


@router.patch("/{device_id}", response_model=DeviceRead)
def update_device(device_id: int, payload: DeviceCreate, db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="not found")

    device.name = payload.name
    device.hostname = payload.hostname
    device.ip_address = payload.ip_address
    device.panorama_id = payload.panorama_id
    device.verify_tls = payload.verify_tls
    device.proxy_via_panorama = payload.proxy_via_panorama
    device.polling_enabled = payload.polling_enabled

    if payload.auth is not None:
        target = device.ip_address or device.hostname
        device.encrypted_api_key = _resolve_auth(payload.auth, target, device.verify_tls)

    if device.proxy_via_panorama and device.panorama_id is None:
        raise HTTPException(status_code=400, detail="proxy_via_panorama requires panorama_id")
    if not device.proxy_via_panorama and not device.encrypted_api_key:
        raise HTTPException(
            status_code=400,
            detail="direct device requires an API key — provide `auth` to set one",
        )

    db.commit()
    db.refresh(device)
    return _to_read(device)


@router.post("/{device_id}/test-connection")
def test_device(device_id: int, db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="not found")

    if device.proxy_via_panorama:
        if not device.panorama or not device.serial:
            raise HTTPException(
                status_code=400,
                detail="proxy_via_panorama set but no Panorama linkage or serial",
            )
        if not device.panorama.encrypted_api_key:
            raise HTTPException(status_code=400, detail="parent Panorama has no API key")
        api_key = decrypt_key(device.panorama.encrypted_api_key)
        client = PanDeviceClient.via_panorama(
            device.panorama.hostname, api_key, device.serial,
            verify_tls=device.panorama.verify_tls,
        )
    else:
        if not device.encrypted_api_key:
            raise HTTPException(status_code=400, detail="device has no API key")
        api_key = decrypt_key(device.encrypted_api_key)
        target = device.ip_address or device.hostname
        client = PanDeviceClient.direct(target, api_key, verify_tls=device.verify_tls)

    try:
        info = client.get_system_info()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "info": info}


@router.delete("/{device_id}", status_code=204)
def delete_device(device_id: int, db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="not found")
    db.delete(device)
    db.commit()
