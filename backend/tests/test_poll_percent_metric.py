"""A metric flagged `unit: percent` has no `max` source — its `current` IS the
percentage (CPU, memory). The poller must give it max=100 + pct=current so it
participates in heat-map color + alert evaluation. Without the flag, a max-less
metric stays pct=None (a raw count we can't turn into a %)."""

from __future__ import annotations

from xml.etree import ElementTree as ET

from app.capacity.services import poller as poller_mod
from app.capacity.services.catalog import Extractor, Fetcher, MetricSpec, Sources
from app.capacity.services.poller import poll_device
from app.core.devices.models.device import Device
from app.core.devices.models.enums import DeviceSource


def _device(db) -> Device:
    dev = Device(
        name="fw-cpu",
        hostname="10.0.0.5",
        ip_address="10.0.0.5",
        source=DeviceSource.DIRECT,
        connected=False,
        verify_tls=False,
        proxy_via_panorama=False,
        polling_enabled=True,
        model="PA-440",  # preset so first-poll metadata heal skips get_system_info
    )
    db.add(dev)
    db.commit()
    db.refresh(dev)
    return dev


class _Client:
    def __init__(self, xml: str):
        self.xml = xml

    def op_xml(self, cmd: str):
        return ET.fromstring(self.xml)

    def get_system_info(self):  # pragma: no cover - model preset, not called
        raise AssertionError("get_system_info should not be called when model is set")


def _percent_metric(unit: str | None) -> MetricSpec:
    return MetricSpec(
        name="mp_cpu",
        category="system",
        description="",
        current=Sources(
            sources=[
                Fetcher(
                    cmd="<show><res/></show>",
                    extract=Extractor(type="xpath_text", xpath=".//val"),
                )
            ]
        ),
        max=None,
        unit=unit,
    )


def _patch(monkeypatch, fake):
    monkeypatch.setattr(
        poller_mod, "build_client_with_fallback", lambda _db, _dev: (fake, "direct")
    )


def test_percent_metric_gets_pct_and_max_100(client, db, monkeypatch):
    dev = _device(db)
    _patch(monkeypatch, _Client("<response><result><val>47.4</val></result></response>"))

    pts = poll_device(db, dev, [_percent_metric("percent")])

    assert len(pts) == 1
    p = pts[0]
    assert p.current == 47.4
    assert p.max == 100.0
    assert p.pct == 47.4  # current passes straight through as the percentage


def test_maxless_metric_without_percent_has_no_pct(client, db, monkeypatch):
    dev = _device(db)
    _patch(monkeypatch, _Client("<response><result><val>47.4</val></result></response>"))

    pts = poll_device(db, dev, [_percent_metric(None)])  # no unit flag

    assert len(pts) == 1
    assert pts[0].current == 47.4
    assert pts[0].max is None
    assert pts[0].pct is None
