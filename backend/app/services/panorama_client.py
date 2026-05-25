"""Panorama client wrapper.

Uses pan-os-python under the hood. We talk to Panorama via the XML API for both
device discovery (read-only) and — eventually — proxied upgrades.

Cert verification: pan-os-python (via requests) will use the host's CA bundle by
default. For a Panorama with a real LetsEncrypt cert this Just Works. For
self-signed labs, set Panorama.verify_tls=False on the model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from panos.errors import PanDeviceError
from panos.panorama import Panorama as PanosPanorama

from app.services.credentials import ResolvedCredential

log = logging.getLogger(__name__)


@dataclass
class ManagedDevice:
    """One row from `show devices all` on Panorama, normalized for our DB."""

    serial: str
    hostname: str | None
    ip_address: str | None
    model: str | None
    sw_version: str | None
    connected: bool
    uptime: str | None
    # HA detail
    ha_state: str | None         # "active", "passive", "tentative", "non-functional", ...
    ha_sync_state: str | None    # "synchronized", "not synchronized", ...
    ha_peer_serial: str | None
    # Content versions
    app_version: str | None
    threat_version: str | None
    av_version: str | None
    wildfire_version: str | None
    url_filtering_version: str | None
    gp_client_version: str | None
    # Panorama assignment
    device_group: str | None
    template_stack: str | None


def _text(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.find(path)
    return found.text.strip() if found is not None and found.text else None


class PanoramaClient:
    """Thin wrapper around panos.panorama.Panorama for our specific operations."""

    def __init__(self, hostname: str, credential: ResolvedCredential, *, verify_tls: bool = True):
        self.hostname = hostname
        self.verify_tls = verify_tls
        self._cred = credential
        self._pano: PanosPanorama | None = None

    def _connect(self) -> PanosPanorama:
        if self._pano is not None:
            return self._pano

        if self._cred.api_key:
            pano = PanosPanorama(self.hostname, api_key=self._cred.api_key)
        elif self._cred.username and self._cred.password:
            pano = PanosPanorama(
                self.hostname,
                api_username=self._cred.username,
                api_password=self._cred.password,
            )
        else:
            raise ValueError("Credential has neither api_key nor username+password")

        # pan-os-python passes verify_ssl through to requests
        pano.verify_ssl = self.verify_tls
        self._pano = pano
        return pano

    # ---- connectivity check ----
    def test_connection(self) -> dict:
        """Hit Panorama with a cheap op command and return identifying info."""
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

    # ---- discovery ----
    def list_managed_devices(self) -> list[ManagedDevice]:
        """Return all devices known to Panorama (connected or not)."""
        pano = self._connect()
        try:
            resp = pano.op("<show><devices><all></all></devices></show>", cmd_xml=False)
        except PanDeviceError as exc:
            raise ConnectionError(f"`show devices all` failed: {exc}") from exc

        # Build a serial -> (device_group, template_stack) map by walking the config
        # via separate op commands. Both can fail gracefully — they're nice-to-haves.
        dg_map = self._fetch_device_group_assignments(pano)
        ts_map = self._fetch_template_stack_assignments(pano)

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
                    uptime=_text(entry, "uptime"),
                    ha_state=_text(entry, "ha/state"),
                    ha_sync_state=_text(entry, "ha/sync-state"),
                    ha_peer_serial=_text(entry, "ha/peer/serial"),
                    app_version=_text(entry, "app-version"),
                    threat_version=_text(entry, "threat-version"),
                    av_version=_text(entry, "av-version"),
                    wildfire_version=_text(entry, "wildfire-version"),
                    url_filtering_version=_text(entry, "url-filtering-version"),
                    gp_client_version=_text(entry, "global-protect-client-package-version"),
                    device_group=dg_map.get(serial),
                    template_stack=ts_map.get(serial),
                )
            )
        return devices

    # ---- helpers ----
    def _fetch_device_group_assignments(self, pano: PanosPanorama) -> dict[str, str]:
        """Return {serial: device_group_name}. Returns {} if the call fails."""
        try:
            resp = pano.op("<show><devicegroups></devicegroups></show>", cmd_xml=False)
        except PanDeviceError as exc:
            log.warning("Could not fetch device groups: %s", exc)
            return {}

        result: dict[str, str] = {}
        for dg in resp.findall(".//devicegroups/entry"):
            dg_name = dg.get("name", "")
            for d in dg.findall("./devices/entry"):
                serial = d.get("name") or _text(d, "serial")
                if serial and dg_name:
                    result[serial] = dg_name
        return result

    def _fetch_template_stack_assignments(self, pano: PanosPanorama) -> dict[str, str]:
        """Return {serial: template_stack_name}. Returns {} if the call fails."""
        try:
            resp = pano.op(
                "<show><template-stack></template-stack></show>", cmd_xml=False
            )
        except PanDeviceError as exc:
            log.warning("Could not fetch template stacks: %s", exc)
            return {}

        result: dict[str, str] = {}
        for ts in resp.findall(".//template-stack/entry"):
            ts_name = ts.get("name", "")
            for d in ts.findall("./devices/entry"):
                serial = d.get("name") or _text(d, "serial")
                if serial and ts_name:
                    result[serial] = ts_name
        return result

    # ---- upgrade proxy (still TODO) ----
    def push_image_to_device(self, serial: str, version: str) -> None:
        raise NotImplementedError

    def install_on_device(self, serial: str, version: str) -> None:
        raise NotImplementedError


def keygen(hostname: str, username: str, password: str, *, verify_tls: bool = True) -> str:
    """Exchange username+password for a long-lived API key.

    This is what users do via the docs link in the UI, but we also expose it
    programmatically so the Settings page can offer a 'Generate API key' helper.
    """
    import httpx

    url = f"https://{hostname}/api/"
    params = {"type": "keygen", "user": username, "password": password}
    with httpx.Client(verify=verify_tls, timeout=15.0) as client:
        resp = client.get(url, params=params)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    if root.get("status") != "success":
        msg = root.findtext(".//msg") or resp.text
        raise ValueError(f"keygen failed: {msg}")
    key = root.findtext(".//key")
    if not key:
        raise ValueError("keygen succeeded but response had no <key>")
    return key
