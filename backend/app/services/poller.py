"""The poller: walk every enabled device, walk the catalog, write samples.

One responsibility, deliberately narrow: take a list of devices and a list of
metrics, fetch what each metric says to fetch, and hand the results to the
SampleStore. Scheduling lives in scheduler.py; client construction lives in
pan_client.py; storage lives in storage.py. This file just orchestrates.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.device import Device
from app.services.catalog import MetricSpec
from app.services.credentials import resolve as resolve_credential
from app.services.pan_client import PanDeviceClient
from app.services.storage import SamplePoint, SampleStore

log = logging.getLogger(__name__)


def _build_client(device: Device) -> PanDeviceClient:
    """Pick direct or panorama-proxied based on the device's flags."""
    if device.proxy_via_panorama:
        if not device.panorama or not device.serial:
            raise ValueError(
                f"Device {device.name} requests Panorama proxy but has no Panorama linkage or serial"
            )
        pano_cred = resolve_credential(device.panorama.credential)
        return PanDeviceClient.via_panorama(
            device.panorama.hostname,
            pano_cred,
            device.serial,
            verify_tls=device.panorama.verify_tls,
        )

    if not device.credential:
        raise ValueError(f"Device {device.name} has no credential and is not proxied via Panorama")
    cred = resolve_credential(device.credential)
    return PanDeviceClient.direct(
        device.hostname if not device.ip_address else device.ip_address,
        cred,
        verify_tls=device.verify_tls,
    )


def poll_device(device: Device, metrics: list[MetricSpec]) -> list[SamplePoint]:
    """Poll one device for every metric in the catalog. Returns the SamplePoints.

    On per-metric failure: log and continue — one broken metric must not poison
    the rest. On client-construction failure: re-raise so the caller can record
    last_poll_error on the device.
    """
    client = _build_client(device)
    now = datetime.now(timezone.utc)
    out: list[SamplePoint] = []

    # Cache responses keyed by command text — many catalog entries share the
    # same op() (e.g. session current and session max both come from
    # `<show><session><info>`). Saves real round-trips.
    cache: dict[str, object] = {}

    def _run(cmd: str):
        if cmd not in cache:
            cache[cmd] = client.op_xml(cmd)
        return cache[cmd]

    for spec in metrics:
        try:
            current_root = _run(spec.current.cmd)
            current = spec.current.extract.extract(current_root)  # type: ignore[arg-type]
            if current is None:
                log.warning("metric %s: current extractor returned None on %s", spec.name, device.name)
                continue

            max_value: float | None = None
            if spec.max is not None:
                max_root = _run(spec.max.cmd)
                max_value = spec.max.extract.extract(max_root)  # type: ignore[arg-type]

            pct: float | None = None
            if max_value and max_value > 0:
                pct = (current / max_value) * 100.0

            out.append(
                SamplePoint(
                    device_id=device.id,
                    metric=spec.name,
                    ts=now,
                    current=current,
                    max=max_value,
                    pct=pct,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("metric %s failed on %s: %s", spec.name, device.name, exc)

    return out


def poll_all(db: Session, metrics: list[MetricSpec], store: SampleStore) -> dict[int, int]:
    """Poll every enabled device and write samples. Returns {device_id: sample_count}."""
    results: dict[int, int] = {}
    devices = db.query(Device).filter(Device.polling_enabled == True).all()  # noqa: E712

    for device in devices:
        try:
            points = poll_device(device, metrics)
            store.write_samples(points)
            results[device.id] = len(points)
            device.last_poll_at = datetime.now(timezone.utc)
            device.last_poll_error = None
            log.info("Polled %s: %d samples", device.name, len(points))
        except Exception as exc:  # noqa: BLE001
            log.exception("Poll failed for %s", device.name)
            device.last_poll_at = datetime.now(timezone.utc)
            device.last_poll_error = str(exc)[:2000]
            results[device.id] = -1
        db.commit()

    return results
