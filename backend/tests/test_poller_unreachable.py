"""Tests for poll_device's "device unreachable" detection.

A direct-polled device that's fully unreachable used to look healthy:
every op command raised, `_sum_sources` swallowed each error, the metric
loop recorded zero samples, and `poll_device` returned `[]` WITHOUT
raising. The caller (`poll_device_task` / `poll_all`) then recorded a
fresh `last_poll_at` and `last_poll_error=None` — a "0-sample success" —
so the UI showed the dead firewall as freshly + successfully polled
("online") indefinitely. (This is the PA-220 "offline device reads as
online" bug.)

The fix: `poll_device` tracks per-command success/failure and raises a
clear "unreachable" error when commands were ATTEMPTED but EVERY one
failed (none succeeded). The distinction these tests pin:

  - all commands fail              -> RAISE (device is unreachable)
  - some succeed, some fail        -> no raise (reachable; skip failed metric)
  - command succeeds, extractor
    finds nothing (metric absent)  -> no raise (reachable; metric just N/A)
  - no commands attempted at all   -> no raise, returns [] (manual/skip path)
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from app.capacity.services import poller as poller_mod
from app.capacity.services.catalog import Extractor, Fetcher, MetricSpec, Sources
from app.capacity.services.poller import poll_device
from app.core.devices.models.device import Device
from app.core.devices.models.enums import DeviceSource


def _make_direct_device(db) -> Device:
    # No credential is set: build_client_with_fallback is monkeypatched in
    # every test below, so encrypted_api_key is never decrypted. Leaving it
    # NULL keeps a secret-shaped literal out of this (public-repo) file and
    # out of the gitleaks PR-range gate — the credential path isn't exercised.
    dev = Device(
        name="branch-5-220",
        hostname="10.0.0.220",
        ip_address="10.0.0.220",
        source=DeviceSource.DIRECT,
        connected=False,
        verify_tls=False,
        proxy_via_panorama=False,
        polling_enabled=True,
        # model preset so the first-poll metadata heal short-circuits and
        # never calls get_system_info (keeps the test about op_xml only).
        model="PA-220",
    )
    db.add(dev)
    db.commit()
    db.refresh(dev)
    return dev


def _count_metric(name: str, cmd: str) -> MetricSpec:
    """A trivial metric: count `.//entry` nodes from `cmd`. xpath_count
    returns 0.0 (a hit) for valid-but-empty XML, so a successful command
    always produces a sample — exactly what we want to prove 'reachable'."""
    return MetricSpec(
        name=name,
        category="system",
        description="",
        current=Sources(
            sources=[Fetcher(cmd=cmd, extract=Extractor(type="xpath_count", xpath=".//entry"))]
        ),
        max=None,
    )


def _absent_metric(name: str, cmd: str) -> MetricSpec:
    """A metric whose command succeeds but whose extractor finds nothing
    (xpath_text on a non-matching path -> None). Models 'reachable, but
    this metric isn't present on this device.'"""
    return MetricSpec(
        name=name,
        category="system",
        description="",
        current=Sources(
            sources=[Fetcher(cmd=cmd, extract=Extractor(type="xpath_text", xpath=".//nonexistent"))]
        ),
        max=None,
    )


class _Client:
    """Fake PanDeviceClient. `op_xml` consults `responses`: a dict mapping
    cmd -> either an XML string (parsed + returned) or an Exception
    instance (raised). Records every cmd attempted in `.calls`."""

    def __init__(self, responses: dict[str, object]):
        self.responses = responses
        self.calls: list[str] = []

    def op_xml(self, cmd: str):
        self.calls.append(cmd)
        r = self.responses[cmd]
        if isinstance(r, Exception):
            raise r
        return ET.fromstring(r)  # type: ignore[arg-type]

    def get_system_info(self):  # pragma: no cover - model preset, not called
        raise AssertionError("get_system_info should not be called when model is set")


def _patch_client(monkeypatch, fake) -> None:
    monkeypatch.setattr(
        poller_mod,
        "build_client_with_fallback",
        lambda _db, _device: (fake, "direct"),
    )


def test_poll_device_raises_when_all_commands_fail(client, db, monkeypatch):
    """Every op command fails -> unreachable -> poll_device RAISES, so the
    caller records last_poll_error instead of a silent 0-sample success."""
    dev = _make_direct_device(db)
    boom = ConnectionError("timed out talking to 10.0.0.220")
    fake = _Client({"<show><a/></show>": boom, "<show><b/></show>": boom})
    _patch_client(monkeypatch, fake)

    metrics = [
        _count_metric("metric_a", "<show><a/></show>"),
        _count_metric("metric_b", "<show><b/></show>"),
    ]

    with pytest.raises(RuntimeError, match="unreachable"):
        poll_device(db, dev, metrics)

    # Both distinct commands were actually attempted (the failure-cache
    # short-circuit must not suppress the FIRST attempt of each command).
    assert set(fake.calls) == {"<show><a/></show>", "<show><b/></show>"}


def test_poll_device_does_not_raise_on_partial_success(client, db, monkeypatch):
    """One command works, another fails. The device is reachable, so
    poll_device must NOT raise — it returns the sample it could get and
    silently skips the failed metric."""
    dev = _make_direct_device(db)
    fake = _Client(
        {
            "<show><ok/></show>": "<response><result><entry/><entry/></result></response>",
            "<show><bad/></show>": ConnectionError("one command flaked"),
        }
    )
    _patch_client(monkeypatch, fake)

    metrics = [
        _count_metric("metric_ok", "<show><ok/></show>"),
        _count_metric("metric_bad", "<show><bad/></show>"),
    ]

    points = poll_device(db, dev, metrics)  # must not raise

    assert {p.metric for p in points} == {"metric_ok"}
    assert points[0].current == 2.0


def test_poll_device_does_not_raise_when_metric_absent(client, db, monkeypatch):
    """Reachable device, command succeeds, extractor finds nothing. No
    sample for that metric, but NOT 'unreachable' — a command did succeed,
    so the failure cache is non-empty and the guard must not trip."""
    dev = _make_direct_device(db)
    fake = _Client({"<show><sys/></show>": "<response><result><up>ok</up></result></response>"})
    _patch_client(monkeypatch, fake)

    points = poll_device(db, dev, [_absent_metric("metric_absent", "<show><sys/></show>")])

    assert points == []  # no sample, but no raise either


def test_poll_device_returns_empty_when_no_metrics(client, db, monkeypatch):
    """No metrics -> no commands attempted -> not 'unreachable'. Returns [].
    Guards the manual poll_all(metrics=[]) / skip-path callers that rely on
    poll_device([]) being a no-op rather than an error."""
    dev = _make_direct_device(db)
    fake = _Client({})
    _patch_client(monkeypatch, fake)

    assert poll_device(db, dev, metrics=[]) == []
    assert fake.calls == []
