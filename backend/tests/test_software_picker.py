"""Version-picker speedup.

A — info-only by default: list_software(check_updates=False) skips the slow
    `request system software check` (the update-server round-trip) and returns
    the device's already-cached catalog. The picker only passes True on an
    explicit "check update server" refresh.
B — parallel bulk fetch: the bulk endpoint fans devices out across a thread
    pool, each worker in its OWN session, and resolves each managing Panorama's
    version once up front.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.command_proxy.pan_client import PanDeviceClient
from app.core.devices.models.device import Device
from app.core.devices.models.enums import DeviceSource, HARole
from app.upgrade.routes import software as software_routes
from app.upgrade.routes.software import (
    AvailableSoftwareBulkIn,
    _fetch_for_device,
    get_available_software_bulk,
)


# ---------- A: list_software(check_updates) ----------

_INFO_XML = (
    "<response status='success'><result><sw-updates><versions>"
    "<entry><version>12.1.7</version><downloaded>yes</downloaded>"
    "<current>no</current><latest>yes</latest><uploaded>no</uploaded>"
    "<filename>PanOS-12.1.7</filename><size>500000</size></entry>"
    "</versions></sw-updates></result></response>"
)


class _FakeProxy:
    def __init__(self):
        self.ops: list[str] = []

    def op(self, xml, cmd_xml=False):
        self.ops.append(xml)
        if "<info>" in xml:
            return ET.fromstring(_INFO_XML)
        return ET.fromstring("<response status='success'><result/></response>")


def _client_with(proxy) -> PanDeviceClient:
    c = PanDeviceClient.__new__(PanDeviceClient)
    c._proxy = proxy
    return c


def test_list_software_default_skips_update_server_check():
    proxy = _FakeProxy()
    out = _client_with(proxy).list_software()
    assert not any("<check>" in op for op in proxy.ops)  # no update-server hit
    assert any("<info>" in op for op in proxy.ops)
    assert out and out[0]["version"] == "12.1.7"


def test_list_software_refresh_does_update_server_check():
    proxy = _FakeProxy()
    _client_with(proxy).list_software(check_updates=True)
    check_i = next(i for i, o in enumerate(proxy.ops) if "<check>" in o)
    info_i = next(i for i, o in enumerate(proxy.ops) if "<info>" in o)
    assert check_i < info_i  # check refreshes the catalog before info reads it


# ---------- _fetch_for_device ----------


class _FakeClient:
    def __init__(self, entries, record):
        self._entries = entries
        self._record = record

    def list_software(self, check_updates=False):
        self._record.append(check_updates)
        return self._entries


def _entry(version="12.1.7"):
    return {
        "version": version, "downloaded": True, "current": False,
        "latest": True, "uploaded": False, "filename": "PanOS-x",
        "released_on": "2026/01/01", "size_kb": "500000",
    }


def _dev_obj(**over):
    base = dict(id=1, name="fw1", sw_version="12.1.4", panorama_id=None)
    base.update(over)
    return SimpleNamespace(**base)


def test_fetch_for_device_propagates_refresh_and_pano_version(monkeypatch):
    rec: list[bool] = []
    monkeypatch.setattr(
        software_routes, "build_client_with_fallback",
        lambda db, d: (_FakeClient([_entry()], rec), "direct"),
    )
    out = _fetch_for_device(MagicMock(), _dev_obj(), refresh=True, pano_version="12.1.9")
    assert rec == [True]  # check_updates threaded through
    assert out.panorama_version == "12.1.9"
    assert out.current_version == "12.1.4"
    assert [e.version for e in out.available] == ["12.1.7"]
    assert out.error is None


def test_fetch_for_device_default_is_info_only(monkeypatch):
    rec: list[bool] = []
    monkeypatch.setattr(
        software_routes, "build_client_with_fallback",
        lambda db, d: (_FakeClient([_entry()], rec), "direct"),
    )
    _fetch_for_device(MagicMock(), _dev_obj(), refresh=False, pano_version=None)
    assert rec == [False]


def test_fetch_for_device_surfaces_error(monkeypatch):
    def _boom(db, d):
        raise ConnectionError("Panorama unreachable")

    monkeypatch.setattr(software_routes, "build_client_with_fallback", _boom)
    out = _fetch_for_device(MagicMock(), _dev_obj(), refresh=False, pano_version=None)
    assert out.available == []
    assert "Panorama unreachable" in (out.error or "")


# ---------- B: bulk endpoint (parallel, real per-thread sessions) ----------
#
# Called directly with the `db` fixture (file-based SQLite → each worker's
# SessionLocal() gets its own connection). `client` resets/creates the schema.


def _seed(db, name, **over) -> Device:
    fields = dict(
        name=name, hostname=f"{name}.local", verify_tls=False,
        proxy_via_panorama=False, polling_enabled=True,
        source=DeviceSource.DIRECT, ha_role=HARole.STANDALONE,
        sw_version="12.1.4",
    )
    fields.update(over)  # let callers override (e.g. panorama_id, proxy_via_panorama)
    d = Device(**fields)
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def test_bulk_fetches_all_devices_info_only_by_default(client, db, monkeypatch):
    refresh_seen: list[bool] = []
    monkeypatch.setattr(
        software_routes, "build_client_with_fallback",
        lambda db_, d: (_FakeClient([_entry()], refresh_seen), "direct"),
    )
    monkeypatch.setattr(software_routes, "panorama_sw_version", lambda d: None)

    ids = [_seed(db, f"fw{i}").id for i in range(3)]
    out = get_available_software_bulk(
        AvailableSoftwareBulkIn(device_ids=ids, refresh=False), db
    )
    assert set(out.results.keys()) == set(ids)  # every device fetched
    for did in ids:
        assert out.results[did].available[0].version == "12.1.7"
        assert out.results[did].error is None
    assert refresh_seen == [False, False, False]  # info-only for all


def test_bulk_refresh_propagates_to_every_device(client, db, monkeypatch):
    refresh_seen: list[bool] = []
    monkeypatch.setattr(
        software_routes, "build_client_with_fallback",
        lambda db_, d: (_FakeClient([_entry()], refresh_seen), "direct"),
    )
    monkeypatch.setattr(software_routes, "panorama_sw_version", lambda d: None)

    ids = [_seed(db, f"fw{i}").id for i in range(3)]
    get_available_software_bulk(
        AvailableSoftwareBulkIn(device_ids=ids, refresh=True), db
    )
    assert refresh_seen == [True, True, True]


def test_bulk_stale_id_surfaces_per_device_not_found(client, db, monkeypatch):
    monkeypatch.setattr(
        software_routes, "build_client_with_fallback",
        lambda db_, d: (_FakeClient([_entry()], []), "direct"),
    )
    monkeypatch.setattr(software_routes, "panorama_sw_version", lambda d: None)

    real = _seed(db, "fw-real").id
    out = get_available_software_bulk(
        AvailableSoftwareBulkIn(device_ids=[real, 99999], refresh=False), db
    )
    assert out.results[real].error is None
    assert out.results[99999].error == "device not found"


def test_bulk_resolves_panorama_version_once_per_panorama(client, db, monkeypatch):
    from app.core.panorama.models.panorama import Panorama

    pano = Panorama(name="pano1", hostname="pano1.local")
    db.add(pano)
    db.commit()
    db.refresh(pano)

    monkeypatch.setattr(
        software_routes, "build_client_with_fallback",
        lambda db_, d: (_FakeClient([_entry()], []), "direct"),
    )
    pano_calls: list[int] = []
    monkeypatch.setattr(
        software_routes, "panorama_sw_version",
        lambda d: (pano_calls.append(d.panorama_id) or "12.1.9"),
    )

    a = _seed(db, "fwa", panorama_id=pano.id, proxy_via_panorama=True)
    b = _seed(db, "fwb", panorama_id=pano.id, proxy_via_panorama=True)
    out = get_available_software_bulk(
        AvailableSoftwareBulkIn(device_ids=[a.id, b.id], refresh=False), db
    )
    # Two devices, one Panorama → its version resolved exactly once (deduped).
    assert pano_calls == [pano.id]
    assert out.results[a.id].panorama_version == "12.1.9"
    assert out.results[b.id].panorama_version == "12.1.9"
