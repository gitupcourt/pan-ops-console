"""dp_cpu must roll up ALL data processors, not just dp0.

`dp_cpu` averages every core across every DP (wildcard the DP element), matching
panos-upgrade-assurance's get_dp_cpu_utilization; `dp_cpu_max` reports the
hottest core so one pegged DP isn't hidden by the average. Exercised here with a
synthetic 2-DP resource-monitor response (we don't have real multi-DP hardware
yet — see the chassis-validation note in catalog/metrics.yaml).
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from app.capacity.services.catalog import Extractor, load_catalog

# Real catalog (tests otherwise load a fake one via CATALOG_PATH).
_REAL_CATALOG = Path(__file__).resolve().parents[2] / "catalog" / "metrics.yaml"

# Two DPs: dp0 cores 10/20, dp1 cores 80/90  ->  avg = 50, max = 90.
_SAMPLE = (
    "<response status='success'><result><resource-monitor><data-processors>"
    "<dp0><minute><cpu-load-average>"
    "<entry><value>10</value></entry><entry><value>20</value></entry>"
    "</cpu-load-average></minute></dp0>"
    "<dp1><minute><cpu-load-average>"
    "<entry><value>80</value></entry><entry><value>90</value></entry>"
    "</cpu-load-average></minute></dp1>"
    "</data-processors></resource-monitor></result></response>"
)
_XPATH = ".//data-processors/*/minute/cpu-load-average/entry/value"


def test_xpath_avg_rolls_up_all_dps():
    val = Extractor(type="xpath_avg", xpath=_XPATH).extract(ET.fromstring(_SAMPLE))
    assert val == 50.0  # (10 + 20 + 80 + 90) / 4 — not just dp0's 15


def test_xpath_max_reports_hottest_core():
    val = Extractor(type="xpath_max", xpath=_XPATH).extract(ET.fromstring(_SAMPLE))
    assert val == 90.0  # the single busiest core across both DPs


def test_xpath_max_returns_none_with_no_matches():
    assert Extractor(type="xpath_max", xpath=_XPATH).extract(
        ET.fromstring("<response><result/></response>")
    ) is None


def test_catalog_dp_metrics_are_wired():
    cat = {m.name: m for m in load_catalog(_REAL_CATALOG)}
    dp, dp_max = cat["dp_cpu"], cat["dp_cpu_max"]
    # both wildcard the DP element so multi-DP boxes are covered
    assert "data-processors/*" in dp.current.sources[0].xpath
    assert "data-processors/*" in dp_max.current.sources[0].xpath
    assert dp.current.sources[0].extract.type == "xpath_avg"
    assert dp_max.current.sources[0].extract.type == "xpath_max"
    # both are percentages (no max source -> poller treats ceiling as 100)
    assert dp.unit == "percent" and dp_max.unit == "percent"
    assert dp.max is None and dp_max.max is None
