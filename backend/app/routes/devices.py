"""Device CRUD."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.device import Device
from app.schemas import DeviceCreate, DeviceRead
from app.services.credentials import resolve as resolve_credential
from app.services.pan_client import PanDeviceClient

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", response_model=list[DeviceRead])
def list_devices(db: Session = Depends(get_db)):
    return db.query(Device).order_by(Device.name).all()


@router.post("", response_model=DeviceRead, status_code=201)
def create_device(payload: DeviceCreate, db: Session = Depends(get_db)):
    device = Device(**payload.model_dump())
    db.add(device)
    db.commit()
    db.refresh(device)
    return device


@router.patch("/{device_id}", response_model=DeviceRead)
def update_device(device_id: int, payload: DeviceCreate, db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(device, k, v)
    db.commit()
    db.refresh(device)
    return device


@router.post("/{device_id}/test-connection")
def test_device(device_id: int, db: Session = Depends(get_db)):
    """Probe a device with `show system info` using its current cred/proxy config."""
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="not found")

    if device.proxy_via_panorama:
        if not device.panorama or not device.serial:
            raise HTTPException(
                status_code=400,
                detail="device requests Panorama proxy but has no Panorama linkage or serial",
            )
        pano_cred = resolve_credential(device.panorama.credential)
        client = PanDeviceClient.via_panorama(
            device.panorama.hostname, pano_cred, device.serial,
            verify_tls=device.panorama.verify_tls,
        )
    else:
        if not device.credential:
            raise HTTPException(status_code=400, detail="device has no credential")
        cred = resolve_credential(device.credential)
        target = device.ip_address or device.hostname
        client = PanDeviceClient.direct(target, cred, verify_tls=device.verify_tls)

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
