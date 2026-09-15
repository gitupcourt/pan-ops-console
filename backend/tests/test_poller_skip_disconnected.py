"""Tests for the poller's "skip known-offline Panorama-imported devices" behavior.

Before this skip existed, every poll cycle tried to talk to every
device whose `polling_enabled=True` — including Panorama-imported
devices that Panorama itself was reporting as `connected=False`. Each
such attempt:

1. Wasted an XML round-trip producing a guaranteed "target not connected"
   error.
2. Generated a misleading "Panorama proxy failed" warning in the logs
   (pre-#48 the same call ALSO wrote `panoramas.reachable=False`, the
   N×1 attribution bug fixed in test_command_proxy.py's
   `test_target_disconnected_does_not_mark_panorama_unhealthy`).
3. Spammed `last_poll_error` on the device with the upstream PAN error
   message, hiding the simple reality that the device is just offline.

The skip turns it into a one-line `last_poll_error="Skipped: Panorama
reports device as disconnected..."` and a `last_poll_at` heartbeat the
operator can see in the UI.

Design choice pinned by these tests:

- Skip applies ONLY to `source=PANORAMA` devices. Direct-added devices
  don't have a reliable `connected` signal (sync only refreshes
  Panorama-imported rows) so a `connected=False` flag on a direct device
  is meaningless, not "the device is offline" — never skip on it.
- Skip writes `last_poll_at = now` so the UI shows a recent heartbeat
  even though no real poll happened. Without that, the dashboard would
  display "Last poll: never" for every disconnected device, which is
  worse UX than "Last poll: 30s ago — skipped: disconnected".
- `db.commit()` happens INSIDE the skip branch so the per-device state
  is durable even if a later device in the loop crashes the worker.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.capacity.services.poller import _is_known_offline, poll_all
from app.core.crypto import encrypt
from app.core.devices.models.device import Device
from app.core.devices.models.enums import DeviceSource
from app.core.panorama.models.panorama import Panorama


# Seed encrypted blobs through the app's own crypto helper: they use the
# process FERNET_KEY, so no key literal lives in this repo.


def _make_panorama(db) -> Panorama:
    pano = Panorama(
        name="pano1",
        hostname="10.0.0.100",
        encrypted_api_key=encrypt("pano-key"),
        verify_tls=False,
        reachable=True,
    )
    db.add(pano)
    db.commit()
    db.refresh(pano)
    return pano


def _make_device(
    db,
    *,
    name: str,
    source: DeviceSource,
    connected: bool,
    panorama: Panorama | None = None,
    serial: str | None = None,
) -> Device:
    dev = Device(
        name=name,
        hostname="10.0.0.1",
        ip_address="10.0.0.1",
        serial=serial,
        source=source,
        connected=connected,
        verify_tls=False,
        proxy_via_panorama=(source == DeviceSource.PANORAMA),
        polling_enabled=True,
        panorama_id=panorama.id if panorama else None,
        encrypted_api_key=encrypt("dev-key"),
    )
    db.add(dev)
    db.commit()
    db.refresh(dev)
    return dev


# ---------- _is_known_offline classification ----------


def test_panorama_imported_disconnected_is_known_offline(client, db):
    pano = _make_panorama(db)
    dev = _make_device(
        db, name="azure-fw", source=DeviceSource.PANORAMA, connected=False,
        panorama=pano, serial="0123",
    )
    assert _is_known_offline(dev) is True


def test_panorama_imported_connected_is_not_known_offline(client, db):
    pano = _make_panorama(db)
    dev = _make_device(
        db, name="ok-fw", source=DeviceSource.PANORAMA, connected=True,
        panorama=pano, serial="0124",
    )
    assert _is_known_offline(dev) is False


def test_direct_added_disconnected_is_not_known_offline(client, db):
    """Direct devices never carry a meaningful `connected` flag (sync
    doesn't refresh them). Skipping based on `connected=False` here
    would silently disable polling for every direct device — never do it.
    """
    dev = _make_device(
        db, name="direct-fw", source=DeviceSource.DIRECT, connected=False,
    )
    assert _is_known_offline(dev) is False


def test_direct_added_connected_is_not_known_offline(client, db):
    """Sanity: direct + connected=True is also not "known offline."
    Just confirms the function doesn't accidentally return True for direct
    devices regardless of the connected flag."""
    dev = _make_device(
        db, name="direct-fw-2", source=DeviceSource.DIRECT, connected=True,
    )
    assert _is_known_offline(dev) is False


# ---------- poll_all skip integration ----------


def test_poll_all_skips_disconnected_panorama_device_without_calling_client(
    client, db
):
    """The disconnected device's poll() must NOT be invoked — that's the
    whole point of the skip. The friendly `last_poll_error` is written
    and `last_poll_at` becomes a recent timestamp so the UI shows a
    heartbeat."""
    pano = _make_panorama(db)
    dev = _make_device(
        db, name="azure-fw", source=DeviceSource.PANORAMA, connected=False,
        panorama=pano, serial="0123",
    )

    # If poll_device were called, this MagicMock-based store would still
    # work, but build_client_with_fallback (called inside poll_device)
    # would try to talk to a fake host and fail. We assert that no real
    # client-build attempt happens by checking the rich error message
    # didn't get overwritten by an upstream exception.
    store = MagicMock()
    result = poll_all(db, metrics=[], store=store)

    assert result == {dev.id: 0}
    store.write_samples.assert_not_called()

    db.refresh(dev)
    assert dev.last_poll_at is not None
    assert dev.last_poll_error is not None
    assert "disconnected" in dev.last_poll_error.lower()
    # Crucial: the friendly skip message wins; nothing about "Panorama
    # proxy failed" or a raw PAN-OS error should appear.
    assert "panorama proxy failed" not in dev.last_poll_error.lower()


def test_poll_all_does_not_skip_connected_panorama_device(client, db, monkeypatch):
    """A connected Panorama device should NOT take the skip path.

    We don't actually want to make a network call in tests, so we
    monkeypatch `poll_device` to record the call and return no samples.
    This isolates the *routing* decision (skip vs. attempt) from the
    network behavior of an actual poll.
    """
    pano = _make_panorama(db)
    dev = _make_device(
        db, name="ok-fw", source=DeviceSource.PANORAMA, connected=True,
        panorama=pano, serial="0124",
    )

    calls = []

    def _fake_poll_device(_db, _device, _metrics):
        calls.append(_device.id)
        return []

    monkeypatch.setattr(
        "app.capacity.services.poller.poll_device",
        _fake_poll_device,
    )

    store = MagicMock()
    result = poll_all(db, metrics=[], store=store)

    assert calls == [dev.id]
    # No samples returned -> 0 samples written, but poll_device WAS called.
    assert result == {dev.id: 0}

    db.refresh(dev)
    # `last_poll_error` is cleared on a successful poll path.
    assert dev.last_poll_error is None


def test_poll_all_does_not_skip_direct_device_with_connected_false(
    client, db, monkeypatch
):
    """The skip is for Panorama-imported devices only. A direct-added
    device with `connected=False` (the default state, since direct
    devices' `connected` is never refreshed) should still be polled.
    Otherwise we'd silently stop polling every direct device, which is
    a worse regression than the bug this whole skip is trying to fix.
    """
    dev = _make_device(
        db, name="direct-fw", source=DeviceSource.DIRECT, connected=False,
    )

    calls = []

    def _fake_poll_device(_db, _device, _metrics):
        calls.append(_device.id)
        return []

    monkeypatch.setattr(
        "app.capacity.services.poller.poll_device",
        _fake_poll_device,
    )

    poll_all(db, metrics=[], store=MagicMock())

    # poll_device was invoked — the direct device was NOT skipped.
    assert calls == [dev.id]


def test_poll_all_skip_does_not_taint_other_devices(client, db, monkeypatch):
    """A single skipped device doesn't break the loop for healthy devices.

    Mix one disconnected Panorama device with one connected one; both
    rows should get processed (the disconnected one skipped, the
    connected one polled). Regression guard: the skip branch's
    `continue` must not short-circuit the outer loop.
    """
    pano = _make_panorama(db)
    offline = _make_device(
        db, name="offline-fw", source=DeviceSource.PANORAMA, connected=False,
        panorama=pano, serial="0125",
    )
    online = _make_device(
        db, name="online-fw", source=DeviceSource.PANORAMA, connected=True,
        panorama=pano, serial="0126",
    )

    polled: list[int] = []

    def _fake_poll_device(_db, _device, _metrics):
        polled.append(_device.id)
        return []

    monkeypatch.setattr(
        "app.capacity.services.poller.poll_device",
        _fake_poll_device,
    )

    result = poll_all(db, metrics=[], store=MagicMock())

    # The offline device was skipped (id appears in result, but
    # poll_device was not called for it). The online device was polled.
    assert offline.id in result
    assert online.id in result
    assert polled == [online.id]  # ONLY the online one
