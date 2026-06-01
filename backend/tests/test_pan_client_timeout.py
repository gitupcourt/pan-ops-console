"""pan_client stamps a bounded socket timeout on every device client.

Regression guard for the "dead DIRECT device pins a poller slot" bug: pan-os-
python defaults PanDevice.timeout to 1200s AND caches the xapi at construction,
so the urlopen socket timeout is whatever the xapi was built with. Setting
`.timeout` afterward (the first attempt, #141) is a no-op. An unreachable mgmt
IP then rode the ~127s OS TCP-connect default per op() (the kernel gives up
before 1200s) — ~6.75 min for one down device's 3 capacity commands, observed
on a down PA-220. The factories now pass the timeout to the CONSTRUCTOR (and
set it on the proxied path's live xapi), which is the only way it reaches the
socket; the builder threads the operator-configurable
Settings.PAN_CLIENT_TIMEOUT_SECONDS through.

These assert on `xapi.timeout` — the value actually read at request time — NOT
the decorative `.timeout` attribute. They construct clients only (pan-os-python
opens no socket until an op() call), so no network or DB is needed.
"""

from __future__ import annotations

from app.core.command_proxy.pan_client import DEFAULT_OP_TIMEOUT_S, PanDeviceClient


def test_direct_applies_default_timeout():
    c = PanDeviceClient.direct("10.0.0.1", "apikey", verify_tls=False)
    assert c._proxy.xapi.timeout == DEFAULT_OP_TIMEOUT_S


def test_direct_applies_explicit_timeout():
    c = PanDeviceClient.direct("10.0.0.1", "apikey", verify_tls=False, timeout_s=7)
    assert c._proxy.xapi.timeout == 7


def test_via_panorama_applies_timeout():
    c = PanDeviceClient.via_panorama(
        "10.0.0.100", "apikey", "0123456789", verify_tls=False, timeout_s=9
    )
    # Set on the proxy's live xapi (read per request) — what op() actually uses.
    assert c._proxy.xapi.timeout == 9


def test_default_timeout_well_under_os_tcp_default():
    # The whole point is failing a dead device fast: the OS TCP-connect default
    # is ~127s, so our default must be comfortably below it (and below the
    # per-device poll cadence) or a down device pins a poller slot.
    assert DEFAULT_OP_TIMEOUT_S <= 60
