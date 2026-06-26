"""mp_cpu must read the management-plane 1-minute CPU average from the
monitoring subsystem (`sys.monitor.*.mp.exports` -> cpu.1minavg), NOT the
unreliable `top -bn1` `%Cpu(s)` first-sample.

Regression: that first sample is the average-since-boot, not the current load.
On a PA-440 the firewall reported ~4% MP CPU while the old extractor stored
~83% (it parsed top's idle field, which read 17% on that first sample). This
test loads the REAL catalog (so it also catches a mis-quoted YAML regex) and
runs mp_cpu's extractor against a representative monitor-state response.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from app.capacity.services.catalog import load_catalog

# Real catalog ships at <repo>/catalog/metrics.yaml; tests otherwise load a
# fake catalog via CATALOG_PATH, so point load_catalog at the real file.
_REAL_CATALOG = Path(__file__).resolve().parents[2] / "catalog" / "metrics.yaml"

# Shape of `show system state filter sys.monitor.*.mp.exports` (slot varies by
# platform; PA-440 is s1). The MP CPU is cpu.1minavg.
_SAMPLE = (
    "<response status='success'><result>"
    "sys.monitor.s1.mp.exports: { 'cpu': { '1minavg': 7, }, "
    "'disks': [ { 'Drive': mmcblk0p2, 'Mount': /x, } ], 'slot': 1, }"
    "</result></response>"
)


def _mp_cpu():
    return next(m for m in load_catalog(_REAL_CATALOG) if m.name == "mp_cpu")


def test_mp_cpu_reads_monitor_state_not_top():
    spec = _mp_cpu()
    cmd = spec.current.sources[0].cmd
    assert "sys.monitor.*.mp.exports" in cmd  # wildcard slot
    assert "resources" not in cmd  # not the unreliable top output


def test_mp_cpu_extracts_1minavg():
    spec = _mp_cpu()
    extractor = spec.current.sources[0].extract
    value = extractor.extract(ET.fromstring(_SAMPLE))
    assert value == 7.0  # the real MP CPU %, not 100 - top_idle
    # percent metric with no max source — the poller treats the ceiling as 100
    assert spec.unit == "percent"
    assert spec.max is None
