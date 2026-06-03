"""Version-ordering + crash-hint helpers behind the upgrade-resilience work.

Context (job #4 incident): hub1fw01 upgraded to 12.1.4-h6 while its Panorama
was on 12.1.2 — firewall ahead of Panorama, which PAN-OS doesn't support. The
post-reboot postcheck couldn't reach the device through Panorama, the
ConnectionError crashed the driver, and the job failed with no recovery path.

These cover the pure pieces of the fix: comparable version tuples (to detect
target > Panorama) and the crash-hint classifier (turns a bare timeout into an
actionable "Panorama may be behind / still re-registering" message).
"""

from __future__ import annotations

from celery.exceptions import SoftTimeLimitExceeded

from app.core.command_proxy.pan_client import version_tuple
from app.upgrade.services.upgrade import _crash_hint, _driver_failure_message


# ---------- version_tuple ----------

def test_version_tuple_parses_hotfix():
    assert version_tuple("12.1.4-h6") == (12, 1, 4, 6)
    assert version_tuple("12.1.2") == (12, 1, 2, 0)
    assert version_tuple("11.2.11") == (11, 2, 11, 0)


def test_version_tuple_empty_is_zeros():
    assert version_tuple("") == (0, 0, 0, 0)
    assert version_tuple(None) == (0, 0, 0, 0)


def test_version_tuple_orders_the_skew_case():
    # The job-#4 skew: target 12.1.4-h6 is NEWER than Panorama 12.1.2 → warn.
    assert version_tuple("12.1.4-h6") > version_tuple("12.1.2")
    # Panorama ahead (or equal) → no warning.
    assert not (version_tuple("12.1.2") > version_tuple("12.1.4-h6"))
    assert not (version_tuple("12.1.4-h6") > version_tuple("12.1.4-h6"))
    # Lower train never outranks a higher one.
    assert not (version_tuple("11.2.11") > version_tuple("12.1.2"))
    # Hotfix ordering within the same maintenance release.
    assert version_tuple("12.1.4-h6") > version_tuple("12.1.4-h2")
    assert version_tuple("12.1.4") < version_tuple("12.1.4-h1")


# ---------- _crash_hint ----------

def test_crash_hint_for_panorama_unreachable():
    exc = ConnectionError(
        "Panorama unreachable and no direct route available for hub1fw01: "
        "The read operation timed out"
    )
    hint = _crash_hint(exc)
    assert "Panorama" in hint and "Retry" in hint


def test_crash_hint_for_bare_timeout():
    assert _crash_hint(TimeoutError("The read operation timed out")) != ""


def test_crash_hint_empty_for_unrelated_error():
    assert _crash_hint(ValueError("totally unrelated")) == ""


# ---------- _driver_failure_message ----------

def test_driver_failure_message_time_limit():
    # A time-limit trip gets the distinct "stopped to free the worker" message
    # (the wedged-slot fix) — not a generic crash string.
    msg = _driver_failure_message(SoftTimeLimitExceeded())
    assert "time limit" in msg.lower()
    assert "Retry" in msg


def test_driver_failure_message_generic_uses_hint():
    exc = ConnectionError(
        "Panorama unreachable and no direct route available for fw1: "
        "The read operation timed out"
    )
    msg = _driver_failure_message(exc)
    assert msg.startswith("Upgrade driver error:")
    assert "Panorama" in msg  # crash hint fired
