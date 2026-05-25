"""Command-proxy core: how every module gets a PAN-OS client.

`build_client_with_fallback` is the canonical entry point — proxy-first,
direct-fallback, with Panorama-health tracking as a side effect. Lifted
from pan-fw-upgrader's services/precheck.py at phase 4b. Capacity's
poller now uses it instead of its own simpler _build_client.

The PanDeviceClient class itself stays in pan_client.py.
"""

from app.core.command_proxy.builder import build_client_with_fallback
from app.core.command_proxy.pan_client import PanDeviceClient, keygen

__all__ = ["PanDeviceClient", "build_client_with_fallback", "keygen"]
