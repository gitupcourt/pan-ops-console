"""Tests for the pre-check classifier.

The classifier is where production-relevant judgment lives — every rule
exists to suppress a known false-positive from the underlying
pan-os-upgrade-assurance library. If a rule regresses, operators get either
spurious red badges (blocking real upgrades) or hidden actual failures
(letting broken upgrades start). Both are bad. So we cover each rule's
positive and negative branches.

Rules under test:
  - _classify_ha             — standalone skip, normal-state pass, abnormal fail
  - _classify_panorama       — direct-attached skip, panorama-managed pass/fail
  - _classify_expired_licenses — TP-only expiry + ATP licensed → pass
  - _classify_dynamic_updates  — wildfire-real-time-only → pass
  - _classify_content_version  — failure is WARN, not FAIL
  - overall_severity         — worst-of aggregation
  - unknown check name       — passthrough fallback
"""

from __future__ import annotations

from app.models.enums import DeviceSource, Severity
from app.services.precheck_classifier import classify, overall_severity


# ---------- HA ----------


def test_ha_standalone_is_skip(make_device):
    d = make_device(ha_state=None)
    sev, reason = classify("ha", {"state": False, "reason": "not in HA"}, d)
    assert sev == Severity.SKIP
    assert "standalone" in reason.lower()


def test_ha_disabled_is_skip(make_device):
    d = make_device(ha_state="disabled")
    sev, _ = classify("ha", {"state": False, "reason": "anything"}, d)
    assert sev == Severity.SKIP


def test_ha_active_state_passes(make_device):
    d = make_device(ha_state="active")
    sev, reason = classify("ha", {"state": False, "reason": "lib said no"}, d)
    assert sev == Severity.PASS
    assert "active" in reason


def test_ha_passive_state_passes(make_device):
    d = make_device(ha_state="passive")
    sev, _ = classify("ha", {"state": False, "reason": "lib said no"}, d)
    assert sev == Severity.PASS


def test_ha_initial_state_fails(make_device):
    """'initial' is a transient state after reboot — fail upgrade preflight."""
    d = make_device(ha_state="initial")
    sev, reason = classify("ha", {"state": False, "reason": ""}, d)
    assert sev == Severity.FAIL
    assert "initial" in reason


def test_ha_suspended_state_fails(make_device):
    d = make_device(ha_state="suspended")
    sev, _ = classify("ha", {"state": False, "reason": ""}, d)
    assert sev == Severity.FAIL


# ---------- Panorama ----------


def test_panorama_direct_device_is_skip(make_device):
    d = make_device(source=DeviceSource.DIRECT)
    sev, _ = classify("panorama", {"state": False, "reason": "no panorama"}, d)
    assert sev == Severity.SKIP


def test_panorama_managed_pass(make_device):
    d = make_device(source=DeviceSource.PANORAMA)
    sev, reason = classify("panorama", {"state": True, "reason": "connected"}, d)
    assert sev == Severity.PASS
    assert reason == "connected"


def test_panorama_managed_fail(make_device):
    d = make_device(source=DeviceSource.PANORAMA)
    sev, reason = classify("panorama", {"state": False, "reason": "lost link"}, d)
    assert sev == Severity.FAIL
    assert reason == "lost link"


# ---------- Expired licenses ----------


def test_expired_licenses_all_valid_passes(device):
    sev, _ = classify("expired_licenses", {"state": True, "reason": "all good"}, device)
    assert sev == Severity.PASS


def test_expired_only_threat_prevention_with_atp_is_pass(make_device):
    """Threat Prevention expiry is a non-issue when Advanced Threat Prevention
    is licensed — TP is functionally superseded by ATP."""
    d = make_device(
        licenses={"feature": [{"name": "Advanced Threat Prevention"}]},
    )
    sev, reason = classify(
        "expired_licenses",
        {"state": False, "reason": "Threat Prevention license is expired"},
        d,
    )
    assert sev == Severity.PASS
    assert "superseded" in reason.lower()


def test_expired_threat_prevention_without_atp_is_fail(make_device):
    d = make_device(licenses={"feature": []})
    sev, _ = classify(
        "expired_licenses",
        {"state": False, "reason": "Threat Prevention license is expired"},
        d,
    )
    assert sev == Severity.FAIL


def test_expired_other_license_still_fails_even_with_atp(make_device):
    """Don't accidentally suppress other expired licenses just because ATP exists."""
    d = make_device(licenses={"feature": [{"name": "Advanced Threat Prevention"}]})
    sev, _ = classify(
        "expired_licenses",
        {"state": False, "reason": "URL Filtering license is expired"},
        d,
    )
    assert sev == Severity.FAIL


# ---------- Dynamic updates ----------


def test_dynamic_updates_clean_passes(device):
    sev, _ = classify(
        "dynamic_updates", {"state": True, "reason": "no jobs running"}, device
    )
    assert sev == Severity.PASS


def test_dynamic_updates_only_wildfire_realtime_is_pass(device):
    """WildFire's real-time schedule fires continuously by design — not a blocker."""
    sev, reason = classify(
        "dynamic_updates",
        {
            "state": False,
            "reason": "schedules fall into test window: wildfire (unpredictable (real-time))",
        },
        device,
    )
    assert sev == Severity.PASS
    assert "wildfire" in reason.lower()


def test_dynamic_updates_multiple_schedules_still_fails(device):
    """If WildFire isn't the only schedule firing, this is a real blocker."""
    sev, _ = classify(
        "dynamic_updates",
        {
            "state": False,
            "reason": "schedules fall into test window: wildfire (unpredictable (real-time)), threats (every-30-mins)",
        },
        device,
    )
    assert sev == Severity.FAIL


# ---------- Content version ----------


def test_content_version_pass(device):
    sev, _ = classify(
        "content_version", {"state": True, "reason": "current"}, device
    )
    assert sev == Severity.PASS


def test_content_version_stale_is_warn_not_fail(device):
    """Stale content shouldn't block upgrades — surface as a warning."""
    sev, _ = classify(
        "content_version", {"state": False, "reason": "stale by 1 release"}, device
    )
    assert sev == Severity.WARN


# ---------- Default / fallback ----------


def test_unknown_check_passthrough_pass(device):
    sev, reason = classify("some_new_check", {"state": True, "reason": "ok"}, device)
    assert sev == Severity.PASS
    assert reason == "ok"


def test_unknown_check_passthrough_fail(device):
    sev, _ = classify("some_new_check", {"state": False, "reason": "nope"}, device)
    assert sev == Severity.FAIL


# ---------- overall_severity aggregation ----------


def test_overall_severity_fail_dominates():
    assert overall_severity([Severity.PASS, Severity.WARN, Severity.FAIL]) == Severity.FAIL


def test_overall_severity_warn_beats_pass():
    assert overall_severity([Severity.PASS, Severity.WARN, Severity.SKIP]) == Severity.WARN


def test_overall_severity_pass_beats_skip():
    assert overall_severity([Severity.PASS, Severity.SKIP]) == Severity.PASS


def test_overall_severity_only_skip():
    assert overall_severity([Severity.SKIP, Severity.SKIP]) == Severity.SKIP


def test_overall_severity_empty_is_skip():
    """Defensive: an empty input shouldn't blow up; treat as nothing-to-say."""
    assert overall_severity([]) == Severity.SKIP
