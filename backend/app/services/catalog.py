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
                      (used for per-DP-core CPU averaging)
      state_value   — value of a key from `show system state` output
                      (handles both decimal and hex)
      text_regex    — regex against the <result> text content; named group
                      `value` is the number
    """

    type: str
    xpath: str | None = None
    key: str | None = None
    pattern: str | None = None

    def extract(self, root: ET.Element) -> float | None:
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
                return float(m.group("value"))
            except (IndexError, ValueError):
                return None

        log.warning("Unknown extractor type: %s", self.type)
        return None


@dataclass
class Fetcher:
    cmd: str
    extract: Extractor


@dataclass
class MetricSpec:
    name: str
    category: str
    description: str
    current: Fetcher
    max: Fetcher | None
    pan_os_min: str | None = None
    pan_os_max: str | None = None
    # "verified" | "probable" | "needs_work" — surfaced in the UI so operators
    # know which metrics to trust at a glance. Does not affect polling.
    status: str = "probable"


def _build_extractor(raw: dict[str, Any]) -> Extractor:
    return Extractor(
        type=raw["type"],
        xpath=raw.get("xpath"),
        key=raw.get("key"),
        pattern=raw.get("pattern"),
    )


def _build_fetcher(raw: dict[str, Any]) -> Fetcher:
    return Fetcher(cmd=raw["cmd"], extract=_build_extractor(raw["extract"]))


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
                current=_build_fetcher(entry["current"]),
                max=_build_fetcher(entry["max"]) if entry.get("max") else None,
                pan_os_min=entry.get("pan_os_min"),
                pan_os_max=entry.get("pan_os_max"),
                status=entry.get("status", "probable"),
            )
        )
    log.info("Loaded %d metrics from catalog %s", len(metrics), path)
    return metrics
