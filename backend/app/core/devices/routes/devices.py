"""Device CRUD + test-connection + fleet-wide aggregates."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.command_proxy.pan_client import PanDeviceClient
from app.core.credentials import decrypt_key, encrypt_key, mint_key
from app.core.devices.models.device import Device
from app.core.devices.schemas import (
    AuthFromApiKey,
    AuthFromUserpass,
    DeviceCreate,
    DeviceRead,
    SerialReplace,
)
from app.core.devices.services import rma
from app.db import get_db

router = APIRouter(prefix="/devices", tags=["devices"])


def _to_read(d: Device) -> DeviceRead:
    # IMPORTANT: every field on DeviceRead must be passed explicitly
    # here, because we build the response from a dict (so the
    # `has_api_key` derived field can include `encrypted_api_key is
    # not None` without exposing the bytes). Missing a field on this
    # dict produces a 500 with ValidationError at response-render
    # time — the bug that hid every device on /inventory when
    # phase-13a added device_group + template_stack to the schema
    # without updating this builder.
    #
    # If you add a field to DeviceRead, ADD IT HERE TOO.
    return DeviceRead.model_validate(
        {
            "id": d.id,
            "name": d.name,
            "hostname": d.hostname,
            "ip_address": d.ip_address,
            "serial": d.serial,
            "model": d.model,
            # Prefer current_version (the upgrader-era column the orchestrator
            # and version-distribution endpoint read); sw_version is the
            # deprecated capacity-era duplicate that's being phased out. Falling
            # back keeps it populated for any device only the legacy poller has
            # touched.
            "sw_version": d.current_version or d.sw_version,
            "source": d.source,
            "panorama_id": d.panorama_id,
            "has_api_key": d.encrypted_api_key is not None,
            "verify_tls": d.verify_tls,
            "proxy_via_panorama": d.proxy_via_panorama,
            "polling_enabled": d.polling_enabled,
            "last_poll_at": d.last_poll_at,
            "last_poll_error": d.last_poll_error,
            "connected": d.connected,
            "last_seen_at": d.last_seen_at,
            "last_refresh_at": d.last_refresh_at,
            "ha_role": d.ha_role,
            "ha_state": d.ha_state,
            "ha_peer_id": d.ha_peer_id,
            "device_group": d.device_group,
            "template_stack": d.template_stack,
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
        # `mint_key` → `keygen` already routes through _friendly_check_error
        # and the ValueError carries a context-tagged operator message
        # (e.g. "Authentication: DNS lookup failed for the device hostname.
        # ..."). Just surface it directly — wrapping with "keygen failed:"
        # would double the prefix.
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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


@router.get("/{device_id}/capacity")
def get_capacity(device_id: int, db: Session = Depends(get_db)):
    """Fetch every `cfg.general.max-*` key the device reports.

    These are the platform limits the firewall actually enforces — same
    values the catalog's max extractors pull individually. Surfacing them
    all together lets an operator answer "what's this box rated for?"
    without polling 50 metrics.
    """
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="not found")

    # Build the right client (same logic as test-connection).
    if device.proxy_via_panorama:
        if not device.panorama or not device.serial:
            raise HTTPException(
                status_code=400,
                detail="proxy_via_panorama set but no Panorama linkage or serial",
            )
        if not device.panorama.encrypted_api_key:
            raise HTTPException(status_code=400, detail="parent Panorama has no API key")
        api_key = decrypt_key(device.panorama.encrypted_api_key, purpose=f"panorama:{device.panorama.id}")
        client = PanDeviceClient.via_panorama(
            device.panorama.hostname, api_key, device.serial,
            verify_tls=device.panorama.verify_tls,
            timeout_s=get_settings().PAN_CLIENT_TIMEOUT_SECONDS,
        )
    else:
        if not device.encrypted_api_key:
            raise HTTPException(status_code=400, detail="device has no API key")
        api_key = decrypt_key(device.encrypted_api_key, purpose=f"device:{device.id}")
        target = device.ip_address or device.hostname
        client = PanDeviceClient.direct(
            target, api_key, verify_tls=device.verify_tls,
            timeout_s=get_settings().PAN_CLIENT_TIMEOUT_SECONDS,
        )

    try:
        resp = client.op_xml(
            "<show><system><state><filter>cfg.general.max-*</filter></state></system></show>"
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    result_el = resp.find(".//result")
    text = result_el.text if result_el is not None and result_el.text else ""

    def _parse(raw: str) -> int | None:
        raw = raw.strip().strip("'\"")
        if not raw or "NO_MATCHES" in raw:
            return None
        try:
            if raw.lower().startswith("0x"):
                return int(raw, 16)
            return int(raw)
        except ValueError:
            return None

    items: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        # Lines look like: "cfg.general.max-address: 10000" or
        # "cfg.general.max-foo: 0x3e8". Some come as "'cfg.general.max-x': NO_MATCHES".
        if not line.startswith("cfg.general.max-") and not line.startswith("'cfg.general.max-"):
            continue
        key, _, value_raw = line.partition(":")
        key = key.strip().strip("'\"").removeprefix("cfg.general.max-")
        parsed = _parse(value_raw)
        items.append({"key": key, "value": parsed, "raw": value_raw.strip()})

    items.sort(key=lambda r: r["key"])
    return {"items": items}


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
        api_key = decrypt_key(device.panorama.encrypted_api_key, purpose=f"panorama:{device.panorama.id}")
        client = PanDeviceClient.via_panorama(
            device.panorama.hostname, api_key, device.serial,
            verify_tls=device.panorama.verify_tls,
            timeout_s=get_settings().PAN_CLIENT_TIMEOUT_SECONDS,
        )
    else:
        if not device.encrypted_api_key:
            raise HTTPException(status_code=400, detail="device has no API key")
        api_key = decrypt_key(device.encrypted_api_key, purpose=f"device:{device.id}")
        target = device.ip_address or device.hostname
        client = PanDeviceClient.direct(
            target, api_key, verify_tls=device.verify_tls,
            timeout_s=get_settings().PAN_CLIENT_TIMEOUT_SECONDS,
        )

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

    # Release any HA peer that still points at this device. `devices.ha_peer_id`
    # is a self-referential FK with NO ACTION on delete, so a lingering pointer
    # (the surviving member of a since-dissolved pair) would otherwise block the
    # delete with an opaque IntegrityError. Nulling it is also the correct
    # outcome — the peer is no longer paired with a device that's going away.
    db.query(Device).filter(Device.ha_peer_id == device_id).update(
        {Device.ha_peer_id: None}, synchronize_session=False
    )

    # The device's child rows (capacity samples, snapshots, prechecks, stage
    # runs, alerts, and — as of migration 0017 — upgrade tasks) are all
    # ON DELETE CASCADE, so they go with the device. Guard the commit anyway: if
    # some future table adds a NO ACTION reference, the operator gets a clear
    # 409 instead of the silent 500 that hid this very failure.
    try:
        db.delete(device)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "Device can't be deleted — it's still referenced by other "
                "records (e.g. an in-flight upgrade job). Resolve those and "
                "try again."
            ),
        ) from exc


@router.post("/{device_id}/replace-serial", response_model=DeviceRead)
def replace_device_serial(
    device_id: int, body: SerialReplace, db: Session = Depends(get_db)
) -> DeviceRead:
    """RMA replace: re-point this device record to a new hardware serial.

    Use when a firewall is RMA'd / factory-reset and comes back with a new
    serial. Keeps this record's name, config, and full history; swaps the
    serial; absorbs any device auto-imported under the new serial; and clears
    the stored API key (the replacement hardware needs fresh auth — re-run
    Test Connection afterward). See `services/rma.py`.
    """
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="not found")
    try:
        rma.replace_serial(db, device, body.new_serial)
        db.commit()
    except rma.SerialReplaceError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Serial replace failed — that serial may already be in use.",
        ) from exc
    db.refresh(device)
    return _to_read(device)


@router.get("/version-distribution")
def version_distribution(db: Session = Depends(get_db)) -> list[dict]:
    """How many devices are running each PAN-OS version.

    Powers the home dashboard's version-distribution frame and the
    "click a version tile to filter inventory" affordance. Returns
    `[{"version": "11.1.4-h7", "count": 50}, ...]` sorted by count
    descending. Devices with no known `current_version` (never
    refreshed, freshly added) are bucketed under `version: null` so
    the operator can spot them and refresh.

    Cheap: one GROUP BY query against the indexed devices table.
    """
    rows = db.execute(
        select(Device.current_version, func.count(Device.id))
        .group_by(Device.current_version)
        .order_by(func.count(Device.id).desc())
    ).all()
    return [{"version": v, "count": c} for v, c in rows]
