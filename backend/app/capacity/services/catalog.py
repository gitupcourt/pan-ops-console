"""Load the metric catalog YAML and provide a typed view of it.

The catalog is data, not code. This module reads it once at startup, validates
the shape, and exposes a list of `MetricSpec` the poller can iterate over. The
poller does not parse YAML itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import yaml

from app.config import get_settings

log = logging.getLogger(__name__)


def _parse_int_maybe_hex(s: str) -> float | None:
    """PAN-OS `show system state` returns values in mixed decimal and hex —
    e.g. `cfg.general.max-address: 10000` next to `cfg.general.max-address-group: 0x3e8`.
    Decode both.
    """
    s = s.strip().strip("'\"")
    if not s:
        return None
    try:
        if s.lower().startswith("0x"):
            return float(int(s, 16))
        return float(s)
    except ValueError:
        return None


@dataclass
class Extractor:
    """How to pull a number out of a parsed XML response.

    Supported types:
      xpath_count   — count of XPath matches
      xpath_text    — float of the first XPath match's text
      xpath_avg     — average of numeric text across ALL XPath matches
      xpath_max     — maximum of numeric text across ALL XPath matches
                      (xpath_avg/xpath_max roll up per-DP-core CPU values)
      state_value   — value of a key from `show system state` output
                      (handles both decimal and hex)
      text_regex    — regex against the <result> text content; named group
                      `value` is the number
    """

    type: str
    xpath: str | None = None
    key: str | None = None
    pattern: str | None = None
    # If True, replace the extracted value `v` with `100 - v`. Used for
    # `show system resources` where the natural number on the line is the
    # idle percentage (so usage = 100 - idle).
    invert: bool = False

    def _post(self, value: float | None) -> float | None:
        if value is None:
            return None
        if self.invert:
            return 100.0 - value
        return value

    def extract(self, root: ET.Element) -> float | None:
        value = self._extract_raw(root)
        return self._post(value)

    def _extract_raw(self, root: ET.Element) -> float | None:
        if self.type == "xpath_count":
            if not self.xpath:
                return None
            return float(len(root.findall(self.xpath)))

        if self.type == "xpath_text":
            if not self.xpath:
                return None
            text = root.findtext(self.xpath)
            if text is None or not text.strip():
                return None
            try:
                return float(text.strip())
            except ValueError:
                return None

        if self.type == "xpath_avg":
            if not self.xpath:
                return None
            vals: list[float] = []
            for el in root.findall(self.xpath):
                if el.text and el.text.strip():
                    try:
                        vals.append(float(el.text.strip()))
                    except ValueError:
                        pass
            if not vals:
                return None
            return sum(vals) / len(vals)

        if self.type == "xpath_max":
            if not self.xpath:
                return None
            vals_max: list[float] = []
            for el in root.findall(self.xpath):
                if el.text and el.text.strip():
                    try:
                        vals_max.append(float(el.text.strip()))
                    except ValueError:
                        pass
            if not vals_max:
                return None
            return max(vals_max)

        if self.type == "state_value":
            if not self.key:
                return None
            result_el = root.find(".//result")
            if result_el is None or not result_el.text:
                return None
            # State output: `cfg.general.max-address: 10000` or `'key': NO_MATCHES`.
            # Match the exact key prefix to avoid `max-address` matching
            # `max-address-group` etc.
            target = self.key + ":"
            for line in result_el.text.splitlines():
                line = line.strip()
                if not (line.startswith(target) or line.startswith(f"'{self.key}':")):
                    continue
                _, _, value = line.partition(":")
                if "NO_MATCHES" in value:
                    return None
                return _parse_int_maybe_hex(value)
            return None

        if self.type == "text_regex":
            if not self.pattern:
                return None
            import re

            result_el = root.find(".//result")
            text = result_el.text if result_el is not None and result_el.text else ""
            m = re.search(self.pattern, text)
            if not m:
                return None
            try:
                value = float(m.group("value"))
            except (IndexError, ValueError):
                return None
            # Optional "total" named group turns this into a percentage —
            # useful for memory where one line gives `... total, ... used`.
            try:
                total = float(m.group("total"))
                if total > 0:
                    return (value / total) * 100.0
            except (IndexError, ValueError):
                pass
            return value

        log.warning("Unknown extractor type: %s", self.type)
        return None


@dataclass
class Fetcher:
    """One (cmd, extract) pair. Multiple of these can be summed via Sources."""
    cmd: str
    extract: Extractor


@dataclass
class Sources:
    """One or more fetchers whose extracted values are SUMMED.

    A single source is the common case (one cmd, one extractor). Multiple
    sources let a metric combine values across distinct commands — e.g.
    "total address objects" = pushed-shared-policy count + running-config
    local vsys count. Sources that return None are skipped; if every source
    returns None, the result is None (treated as "no data this cycle").
    """
    sources: list[Fetcher]


@dataclass
class MetricSpec:
    name: str
    category: str
    description: str
    current: Sources
    max: Sources | None
    pan_os_min: str | None = None
    pan_os_max: str | None = None
    # "verified" | "probable" | "needs_work" — surfaced in the UI so operators
    # know which metrics to trust at a glance. Does not affect polling.
    status: str = "probable"
    # "percent" → `current` is already a 0-100 utilization figure (CPU, memory),
    # so the poller treats the ceiling as 100 and derives pct == current. None →
    # a raw count that needs a `max` source to become a percentage.
    unit: str | None = None


def _build_extractor(raw: dict[str, Any]) -> Extractor:
    return Extractor(
        type=raw["type"],
        xpath=raw.get("xpath"),
        key=raw.get("key"),
        pattern=raw.get("pattern"),
        invert=bool(raw.get("invert", False)),
    )


def _build_fetcher(raw: dict[str, Any]) -> Fetcher:
    return Fetcher(cmd=raw["cmd"], extract=_build_extractor(raw["extract"]))


def _build_sources(raw: dict[str, Any] | None) -> Sources | None:
    """Accept either a single fetcher dict {cmd, extract} or {sources: [...]}.

    Both forms produce a Sources object; the single-fetcher case becomes a
    one-element list so the poller can treat everything uniformly.
    """
    if raw is None:
        return None
    if "sources" in raw:
        return Sources(sources=[_build_fetcher(s) for s in raw["sources"]])
    return Sources(sources=[_build_fetcher(raw)])


def load_catalog(path: str | Path | None = None) -> list[MetricSpec]:
    """Read metrics.yaml and return validated MetricSpec entries."""
    path = Path(path or get_settings().CATALOG_PATH)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    metrics: list[MetricSpec] = []
    for entry in raw.get("metrics", []):
        metrics.append(
            MetricSpec(
                name=entry["name"],
                category=entry["category"],
                description=entry.get("description", ""),
                current=_build_sources(entry["current"]),
                max=_build_sources(entry.get("max")),
                pan_os_min=entry.get("pan_os_min"),
                pan_os_max=entry.get("pan_os_max"),
                status=entry.get("status", "probable"),
                unit=entry.get("unit"),
            )
        )
    log.info("Loaded %d metrics from catalog %s", len(metrics), path)
    return metrics
