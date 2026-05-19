"""Panorama client — device discovery for the capacity analyzer."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from panos.errors import PanDeviceError
from panos.panorama import Panorama as PanosPanorama

log = logging.getLogger(__name__)


@dataclass
class ManagedDevice:
    serial: str
    hostname: str | None
    ip_address: str | None
    model: str | None
    sw_version: str | None
    connected: bool


def _text(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.find(path)
    return found.text.strip() if found is not None and found.text else None


class PanoramaClient:
    def __init__(self, hostname: str, api_key: str, *, verify_tls: bool = True):
        self.hostname = hostname
        self.verify_tls = verify_tls
        self._api_key = api_key
        self._pano: PanosPanorama | None = None

    def _connect(self) -> PanosPanorama:
        if self._pano is not None:
            return self._pano
        pano = PanosPanorama(self.hostname, api_key=self._api_key)
        pano.verify_ssl = self.verify_tls
        self._pano = pano
        return pano

    def test_connection(self) -> dict:
        pano = self._connect()
        try:
            resp = pano.op("<show><system><info></info></system></show>", cmd_xml=False)
        except PanDeviceError as exc:
            raise ConnectionError(f"Panorama op() failed: {exc}") from exc
        info = resp.find(".//system")
        return {
            "hostname": _text(info, "hostname"),
            "model": _text(info, "model"),
            "sw_version": _text(info, "sw-version"),
            "serial": _text(info, "serial"),
            "uptime": _text(info, "uptime"),
        }

    def list_managed_devices(self) -> list[ManagedDevice]:
        pano = self._connect()
        try:
            resp = pano.op("<show><devices><all></all></devices></show>", cmd_xml=False)
        except PanDeviceError as exc:
            raise ConnectionError(f"`show devices all` failed: {exc}") from exc

        devices: list[ManagedDevice] = []
        for entry in resp.findall(".//devices/entry"):
            serial = _text(entry, "serial") or entry.get("name") or ""
            if not serial:
                continue
            devices.append(
                ManagedDevice(
                    serial=serial,
                    hostname=_text(entry, "hostname"),
                    ip_address=_text(entry, "ip-address"),
                    model=_text(entry, "model"),
                    sw_version=_text(entry, "sw-version"),
                    connected=(_text(entry, "connected") or "").lower() == "yes",
                )
            )
        return devices
