"""pan_client stamps a bounded socket timeout on every device client.

Regression guard for the "dead DIRECT device pins a poller slot" bug: pan-os-
python leaves PanDevice.timeout unset by default, so an unreachable mgmt IP
rode the OS TCP-connect default (~127s) per op() — ~6.75 min for one down
device's 3 capacity commands, observed on a down PA-220. The factories now
stamp a bounded timeout on the connection-holding object; the builder threads
the operator-configurable Settings.PAN_CLIENT_TIMEOUT_SECONDS through.

These construct clients only (pan-os-python is lazy — no socket opens until an
op() call), so no network or DB is needed.
"""

from __future__ import annotations

from app.core.command_proxy.pan_client import DEFAULT_OP_TIMEOUT_S, PanDeviceClient


def test_direct_applies_default_timeout():
    c = PanDeviceClient.direct("10.0.0.1", "apikey", verify_tls=False)
    assert c._proxy.timeout == DEFAULT_OP_TIMEOUT_S


def test_direct_applies_explicit_timeout():
    c = PanDeviceClient.direct("10.0.0.1", "apikey", verify_tls=False, timeout_s=7)
    assert c._proxy.timeout == 7


def test_via_panorama_applies_timeout():
    c = PanDeviceClient.via_panorama(
        "10.0.0.100", "apikey", "0123456789", verify_tls=False, timeout_s=9
    )
    # The proxied op() rides Panorama's connection; the proxy mirrors the value.
    assert c._proxy.timeout == 9


def test_default_timeout_well_under_os_tcp_default():
    # The whole point is failing a dead device fast: the OS TCP-connect default
    # is ~127s, so our default must be comfortably below it (and below the
    # per-device poll cadence) or a down device pins a poller slot.
    assert DEFAULT_OP_TIMEOUT_S <= 60
