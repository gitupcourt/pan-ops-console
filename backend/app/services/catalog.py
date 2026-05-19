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


@dataclass
class Extractor:
    """How to pull a number out of a parsed XML response."""

    type: str  # "xpath_count" | "xpath_text" | "state_value"
    xpath: str | None = None
    key: str | None = None

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
        if self.type == "state_value":
            # `show system state` returns key: value lines wrapped in <result>.
            # Find the line matching our key and parse the value out.
            if not self.key:
                return None
            result_el = root.find(".//result")
            if result_el is None or not result_el.text:
                return None
            for line in result_el.text.splitlines():
                line = line.strip()
                if not line.startswith(self.key):
                    continue
                # Format is typically: cfg.general.max-address: 80000
                _, _, value = line.partition(":")
                value = value.strip().strip("'\"")
                try:
                    return float(value)
                except ValueError:
                    return None
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
