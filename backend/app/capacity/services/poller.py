"""The poller: walk every enabled device, walk the catalog, write samples.

As of phase 4b, client construction uses `core.command_proxy.build_client_with_fallback`
— the proxy-first / direct-fallback pattern lifted from pan-fw-upgrader. The
poller gets the same Panorama-health-tracking side effects every other module
does, which means a Panorama outage updates `panoramas.reachable` and the UI's
"Panorama unreachable" banner via the capacity polling path, not just via
upgrade jobs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.alerts.services.evaluator import evaluate_device_samples
from app.capacity.services.catalog import MetricSpec, Sources
from app.capacity.services.storage import SamplePoint, SampleStore
from app.core.command_proxy.builder import build_client_with_fallback
from app.core.devices.models.device import Device
from app.core.devices.models.enums import DeviceSource

log = logging.getLogger(__name__)


def _is_known_offline(device: Device) -> bool:
    """True for Panorama-imported devices that Panorama currently reports
    as not connected.

    Panorama's `connected` field on `show devices all` is authoritative
    for "is this device reachable via Panorama right now." For
    Panorama-imported devices (`source=PANORAMA`), we trust that signal —
    polling a known-offline device through Panorama just produces a "target
    not connected" error per round-trip, which is noise, wastes the call,
    and (pre-#48) used to mis-attribute as "Panorama unreachable."

    Excluded: direct-added devices. We don't refresh their `connected`
    flag (sync only runs against Panorama), so it's permanently False
    for direct devices and unsafe to use as a skip signal there.
    """
    return device.source == DeviceSource.PANORAMA and not device.connected


def _lazy_populate_device_metadata(db: Session, device: Device, client) -> None:
    """Fill in `device.model` (and adjacent metadata) on the first
    successful poll of a freshly-added direct device.

    Direct-added devices land in the DB with `model = NULL` because the
    poller doesn't gather it and the operator may never have clicked
    "Test" (which calls `get_system_info` and populates these fields).
    That leaves them missing from the Capacity Analyzer heat-map (which
    groups by model — null-model devices were dropped) and from any
    UI that surfaces model badges.

    This lazy-populate runs ONLY when `device.model is None` so the cost
    is one-time per device: an extra `get_system_info()` round-trip on
    the device's first successful poll cycle. Once `model` is set the
    branch never fires again.

    Best-effort: if the probe fails we swallow the exception and let the
    regular metric polling proceed. The next poll cycle will retry.
    Panorama-imported devices get `model` from the sync flow, so this
    is effectively direct-device-only in practice.
    """
    if device.model:
        return
    try:
        info = client.get_system_info()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Could not probe %s for metadata (model/serial/version): %s",
            device.name, exc,
        )
        return
    if info.model:
        device.model = info.model
    if info.serial and not device.serial:
        device.serial = info.serial
    if info.sw_version:
        device.current_version = info.sw_version
        device.sw_version = info.sw_version  # transitional dup; see Device model
    if info.hostname and not device.hostname:
        device.hostname = info.hostname
    db.commit()
    log.info(
        "Populated metadata for %s on first poll: model=%s sw=%s",
        device.name, info.model, info.sw_version,
    )


def poll_device(
    db: Session, device: Device, metrics: list[MetricSpec]
) -> list[SamplePoint]:
    client, route = build_client_with_fallback(db, device)
    if route == "direct" and device.proxy_via_panorama:
        log.info("Polling %s direct (Panorama proxy unavailable)", device.name)

    # First-poll metadata heal: if we got a working client and the device
    # has never had its model recorded, grab system-info now. Cheap
    # one-time cost per device; downstream views (Capacity heat-map, UI
    # model badges) get correct labels without operator action.
    _lazy_populate_device_metadata(db, device, client)

    now = datetime.now(timezone.utc)
    out: list[SamplePoint] = []

    # Cache responses keyed by command text so metrics that share a command
    # (e.g. session current and session max both come from `<show><session><info>`)
    # don't re-query the device.
    cache: dict[str, object] = {}
    # Track per-command failures so we can tell "device unreachable" (every
    # command failed) apart from "reachable, but this metric isn't present"
    # (some commands succeeded; an extractor just returned None). Caching the
    # failure also avoids re-issuing — and re-timing-out on — a command that
    # already failed this cycle.
    failed: dict[str, Exception] = {}

    def _run(cmd: str):
        if cmd in cache:
            return cache[cmd]
        if cmd in failed:
            raise failed[cmd]
        try:
            cache[cmd] = client.op_xml(cmd)
        except Exception as exc:
            failed[cmd] = exc
            raise
        return cache[cmd]

    def _sum_sources(srcs: Sources) -> float | None:
        """Run every fetcher in `srcs` and sum the non-None extractions.

        Returns None only if EVERY source returned None (truly no data).
        A source returning 0 contributes 0 to the sum, which is what we
        want for things like "no local config" + "60 pushed objects".
        """
        total = 0.0
        any_hit = False
        for f in srcs.sources:
            try:
                root = _run(f.cmd)
            except Exception as exc:  # noqa: BLE001
                # Some sources legitimately fail on some firewalls (e.g.
                # `show config running` returns 0 through Panorama proxy).
                # Log and skip — don't poison the sum.
                log.debug("source cmd %r failed: %s", f.cmd, exc)
                continue
            v = f.extract.extract(root)
            if v is not None:
                total += v
                any_hit = True
        return total if any_hit else None

    for spec in metrics:
        try:
            current = _sum_sources(spec.current)
            if current is None:
                # Expected + common: the metric simply isn't present on this
                # device this cycle — e.g. a feature like SSL decryption isn't
                # configured, so its counter is absent. DEBUG, not WARNING: at
                # the normal INFO level this is pure per-device-per-cycle noise.
                # A genuinely broken extractor still shows up as a gap in the
                # Capacity charts; enable DEBUG to diagnose one.
                log.debug("metric %s: no current value on %s; skipping this cycle", spec.name, device.name)
                continue

            max_value: float | None = None
            if spec.max is not None:
                max_value = _sum_sources(spec.max)
            elif spec.unit == "percent":
                # The value is already a 0-100 utilization figure (CPU, memory).
                # Treat the ceiling as 100 so pct == current and the metric gets
                # heat-map color + alert evaluation like every other metric.
                max_value = 100.0

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

    # If we issued commands but EVERY one failed (none succeeded), the device
    # is unreachable — surface it as an error instead of returning an empty
    # "0-sample success". Without this, a down direct-polled device looks
    # freshly + successfully polled (last_poll_at fresh, no error) and reads as
    # "online" indefinitely. The caller records last_poll_error from this, and
    # the per-device failure-rewind retries it on the normal cadence.
    if failed and not cache:
        raise RuntimeError(
            f"device unreachable: all {len(failed)} poll command(s) failed "
            f"(e.g. {next(iter(failed.values()))})"
        )

    return out


def poll_all(db: Session, metrics: list[MetricSpec], store: SampleStore) -> dict[int, int]:
    results: dict[int, int] = {}
    devices = db.query(Device).filter(Device.polling_enabled == True).all()  # noqa: E712

    for device in devices:
        # Cheap pre-check: skip Panorama-imported devices that Panorama
        # currently reports as disconnected. Saves the wasted XML round-
        # trip and avoids the (pre-#48) misleading "Panorama proxy failed"
        # log noise per cycle per offline device. The next Panorama sync
        # will refresh `connected` so a device coming back online resumes
        # polling at the next sync interval.
        if _is_known_offline(device):
            device.last_poll_at = datetime.now(timezone.utc)
            device.last_poll_error = (
                "Skipped: Panorama reports device as disconnected. "
                "Polling will resume automatically once Panorama sees the "
                "device come back online."
            )
            results[device.id] = 0
            log.info("Skipped %s: Panorama reports disconnected", device.name)
            db.commit()
            continue

        try:
            points = poll_device(db, device, metrics)
            store.write_samples(points)
            # Evaluate the fresh samples against the alert rule set.
            # Mutates the session but doesn't commit — the per-device
            # commit below picks up both the device-status update and
            # any alert open/close/escalate state in one transaction.
            evaluate_device_samples(db, device.id, points)
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
