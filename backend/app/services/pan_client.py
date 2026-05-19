"""PAN-OS device client — capacity-analyzer flavor.

Strictly read-only. Two construction paths matching pan-fw-upgrader:

* `PanDeviceClient.direct(...)` — talk straight to the firewall.
* `PanDeviceClient.via_panorama(...)` — route ops through Panorama using
  target-serial (for devices we can't reach directly).

The capacity poller only needs `op_xml()` (run an arbitrary XML op command and
get the parsed response) — everything else in the metric catalog is built on
top of that. Kept narrow on purpose; the upgrade UI's pan_client has the full
fat surface and the two converge cleanly when the apps merge.
"""

from __future__ import annotations

import logging
from xml.etree import ElementTree as ET

from panos.errors import PanDeviceError
from panos.firewall import Firewall as PanosFirewall
from panos.panorama import Panorama as PanosPanorama

from app.services.credentials import ResolvedCredential

log = logging.getLogger(__name__)


class PanDeviceClient:
    """Read-only operations against a single firewall — direct or Panorama-proxied."""

    def __init__(self, device: PanosFirewall):
        self._device = device

    # ---------- factories ----------

    @classmethod
    def direct(cls, host: str, cred: ResolvedCredential, *, verify_tls: bool = True) -> "PanDeviceClient":
        if cred.api_key:
            fw = PanosFirewall(host, api_key=cred.api_key)
        else:
            fw = PanosFirewall(host, api_username=cred.username, api_password=cred.password)
        fw.verify_ssl = verify_tls
        return cls(fw)

    @classmethod
    def via_panorama(
        cls,
        panorama_host: str,
        panorama_cred: ResolvedCredential,
        target_serial: str,
        *,
        verify_tls: bool = True,
    ) -> "PanDeviceClient":
        if panorama_cred.api_key:
            pano = PanosPanorama(panorama_host, api_key=panorama_cred.api_key)
        else:
            pano = PanosPanorama(
                panorama_host,
                api_username=panorama_cred.username,
                api_password=panorama_cred.password,
            )
        pano.verify_ssl = verify_tls
        fw = PanosFirewall(serial=target_serial)
        pano.add(fw)
        return cls(fw)

    # ---------- the one operation the poller actually needs ----------

    def op_xml(self, xml_cmd: str) -> ET.Element:
        """Run an arbitrary XML op command and return the parsed response.

        The metric catalog supplies the XML strings; this just executes them.
        """
        try:
            return self._device.op(xml_cmd, cmd_xml=False)
        except PanDeviceError as exc:
            raise ConnectionError(f"op() failed for cmd {xml_cmd!r}: {exc}") from exc

    # ---------- light system probe (used when registering a device) ----------

    def get_system_info(self) -> dict:
        resp = self.op_xml("<show><system><info></info></system></show>")
        info = resp.find(".//system")
        if info is None:
            return {}

        def _t(path: str) -> str | None:
            el = info.find(path)
            return el.text.strip() if el is not None and el.text else None

        return {
            "hostname": _t("hostname"),
            "serial": _t("serial"),
            "model": _t("model"),
            "sw_version": _t("sw-version"),
            "uptime": _t("uptime"),
        }


def keygen(hostname: str, username: str, password: str, *, verify_tls: bool = True) -> str:
    """Exchange username+password for a long-lived API key (works for fw or Panorama)."""
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
