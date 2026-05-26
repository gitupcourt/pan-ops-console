"""Alerts enums.

Lowercase `.value` strings match the Postgres ENUM type emitted by
migration 0006. The enum discipline documented in CLAUDE.md ("never
use plain SQLAlchemy Enum; always go through py_enum_column") applies
here.
"""

from __future__ import annotations

from enum import Enum


class AlertSeverity(str, Enum):
    """Severity bands an alert can fire at.

    Operators see one alert per (device, metric) — the row's severity
    is whichever threshold is currently the worst that's firing. So
    going from 78% → 92% on the same metric escalates the existing
    alert from `warning` to `critical` rather than opening a new one.
    """

    WARNING = "warning"
    CRITICAL = "critical"
