"""Translate raw pan-os-upgrade-assurance results into 4-state severities.

The library returns binary pass/fail with a one-line reason. We layer custom
rules on top so that:

  - HA failing is only a real failure when the state is genuinely abnormal
    (not when the device is intentionally standalone).
  - Panorama connectivity is irrelevant for direct-attached devices.
  - License expiry is OK for a feature that's been superseded
    (Threat Prevention -> Advanced Threat Prevention).
  - WildFire real-time updates aren't a "scheduled job" you should worry about.
  - Stale content is a yellow flag, not a red one.

Every rule takes (raw, device) and returns (Severity, reason). The default
fallback is PASS/FAIL based on the raw boolean.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from app.models.enums import DeviceSource, Severity

if TYPE_CHECKING:
    from app.models.device import Device


# Per the PAN-OS HA states doc:
# https://docs.paloaltonetworks.com/ngfw/administration/high-availability/ha-firewall-states
# These are the operating states we consider "normal" for an HA member.
NORMAL_HA_STATES: set[str] = {
    "active",
    "passive",
    "active-primary",
    "active-secondary",
}


# ---------- per-check rules ----------


def _classify_ha(raw: dict, device: "Device") -> tuple[Severity, str]:
    """Don't fail standalone devices. Only fail when the HA state is abnormal."""
    state = (device.ha_state or "").strip().lower()
    if not state or state == "disabled":
        return (Severity.SKIP, "Standalone device — HA not configured")
    if state in NORMAL_HA_STATES:
        return (Severity.PASS, f"HA state: {state}")
    return (
        Severity.FAIL,
        f"HA state '{state}' is not a normal operating state "
        f"(expected one of: {', '.join(sorted(NORMAL_HA_STATES))})",
    )


def _classify_panorama(raw: dict, device: "Device") -> tuple[Severity, str]:
    """Skip for direct-attached devices; otherwise pass through."""
    if device.source != DeviceSource.PANORAMA:
        return (Severity.SKIP, "Direct-attached device — Panorama connectivity not applicable")
    if raw["state"]:
        return (Severity.PASS, raw["reason"] or "Connected to Panorama")
    return (Severity.FAIL, raw["reason"])


def _classify_expired_licenses(raw: dict, device: "Device") -> tuple[Severity, str]:
    """Threat Prevention expiry is a non-issue if Advanced Threat Prevention is licensed."""
    if raw["state"]:
        return (Severity.PASS, raw["reason"] or "All licenses valid")

    reason = raw["reason"] or ""
    has_atp = _has_license(device, "Advanced Threat Prevention")
    only_tp = (
        "Threat Prevention" in reason
        and "Advanced Threat Prevention" not in reason
    )

    if only_tp and has_atp:
        return (
            Severity.PASS,
            "Threat Prevention is expired but superseded by Advanced Threat Prevention "
            "(no upgrade-blocker)",
        )
    return (Severity.FAIL, reason)


def _classify_dynamic_updates(raw: dict, device: "Device") -> tuple[Severity, str]:
    """WildFire's real-time schedule fires continuously — that's a feature, not a problem."""
    if raw["state"]:
        return (Severity.PASS, raw["reason"] or "No dynamic update jobs running")

    reason = (raw["reason"] or "").lower()
    # Match the library's wording exactly: "wildfire (unpredictable (real-time))"
    has_wildfire_realtime = "wildfire" in reason and "real-time" in reason
    # If the only mentioned schedule is wildfire real-time, treat as pass.
    # Heuristic: count comma-separated schedule references; 1 = just wildfire.
    referenced = reason.split("schedules fall into test window:")[-1]
    schedule_count = referenced.count(",") + 1 if referenced.strip() else 0

    if has_wildfire_realtime and schedule_count <= 1:
        return (
            Severity.PASS,
            "Only the WildFire real-time update schedule is active — by design, not a blocker",
        )
    return (Severity.FAIL, raw["reason"])


def _classify_content_version(raw: dict, device: "Device") -> tuple[Severity, str]:
    """Being one release behind on content is a warning, not a hard fail."""
    if raw["state"]:
        return (Severity.PASS, raw["reason"] or "Content version current")
    return (Severity.WARN, raw["reason"])


# ---------- dispatch ----------

_RULES: dict[str, Callable[[dict, "Device"], tuple[Severity, str]]] = {
    "ha": _classify_ha,
    "panorama": _classify_panorama,
    "expired_licenses": _classify_expired_licenses,
    "dynamic_updates": _classify_dynamic_updates,
    "content_version": _classify_content_version,
}


def classify(check_name: str, raw: dict, device: "Device") -> tuple[Severity, str]:
    rule = _RULES.get(check_name)
    if rule is not None:
        return rule(raw, device)
    # Default: pass through binary as PASS/FAIL with the library's reason.
    sev = Severity.PASS if raw.get("state") else Severity.FAIL
    return (sev, raw.get("reason") or ("OK" if raw.get("state") else "Failed"))


def overall_severity(severities: list[Severity]) -> Severity:
    """Worst-of for the row badge. FAIL > WARN > PASS > SKIP."""
    if Severity.FAIL in severities:
        return Severity.FAIL
    if Severity.WARN in severities:
        return Severity.WARN
    if Severity.PASS in severities:
        return Severity.PASS
    return Severity.SKIP


# ---------- helpers ----------


def _has_license(device: "Device", feature_name: str) -> bool:
    """Best-effort lookup against the cached `licenses` JSON on the device.

    The structure varies by PAN-OS version; we accept either:
      {"feature": [{"name": "Advanced Threat Prevention", ...}, ...]}
      ["Advanced Threat Prevention", ...]
      {"Advanced Threat Prevention": {...}}
    """
    licenses = device.licenses
    if not licenses:
        return False
    if isinstance(licenses, dict):
        if feature_name in licenses:
            return True
        # nested {"licenses": {"entry": [{"feature": "..."}, ...]}} or similar
        for v in licenses.values():
            if isinstance(v, list):
                for entry in v:
                    if isinstance(entry, dict) and (
                        entry.get("feature") == feature_name
                        or entry.get("name") == feature_name
                    ):
                        return True
            elif isinstance(v, dict) and v.get("feature") == feature_name:
                return True
    elif isinstance(licenses, list):
        for entry in licenses:
            if isinstance(entry, str) and entry == feature_name:
                return True
            if isinstance(entry, dict) and (
                entry.get("feature") == feature_name or entry.get("name") == feature_name
            ):
                return True
    return False
