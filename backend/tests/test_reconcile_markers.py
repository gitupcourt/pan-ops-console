"""Tests for state reconciliation at job start.

reconcile_markers_with_device_state corrects phase markers that have
diverged from the device's actual state. The function is what makes
"Retry job" robust to:

  - A user manually finishing an install outside the app (markers
    don't know; device says "yes I'm at target")
  - PAN-OS silently ignoring resume_ha (markers say "resumed"; device
    is still suspended)
  - A previous run crashing mid-flow with markers in a partial state

The probe inside the function is the only DB-touching bit; we monkeypatch
it so these tests stay fast and don't require a live PAN session.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.devices.models.enums import DeviceSource, HARole
from app.upgrade.services import upgrade as upgrade_svc


def _device(**overrides):
    base = {
        "id": 1,
        "name": "fw-01",
        "hostname": "fw-01",
        "ip_address": "10.0.0.1",
        "serial": "0123",
        "source": DeviceSource.DIRECT,
        "ha_role": HARole.PASSIVE,
        "ha_state": "passive",
        "ha_peer_id": 2,
        "current_version": "11.1.4-h7",
        "model": "PA-440",
        "licenses": None,
        "verify_tls": True,
        "proxy_via_panorama": False,
        "panorama_id": None,
        "credential_id": None,
        "device_group": None,
        "template_stack": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def job():
    return SimpleNamespace(id=1, target_version="11.1.4-h7")


@pytest.fixture
def task():
    return SimpleNamespace(id=1, progress={"completed_phases": []})


@pytest.fixture(autouse=True)
def stub_probe_and_client(monkeypatch):
    """Replace the live probe + pan client with no-ops so the helper can run
    in isolation.

    Two subtle things:
      - `from app.upgrade.services import precheck as precheck_svc` (local
        import inside reconcile_markers_...) resolves to the PACKAGE
        attribute already loaded on app.upgrade.services — patching
        sys.modules wouldn't intercept it. We patch the real module
        attribute instead.
      - The real probe_device would mutate the device's fields with
        whatever MagicMock returns (turning current_version into a
        MagicMock object) and break the subsequent string compare. So
        we substitute a no-op directly on the loaded module."""
    from app.upgrade.services import precheck as real_precheck  # noqa: PLC0415

    monkeypatch.setattr(upgrade_svc, "_client_for", lambda d: MagicMock())
    monkeypatch.setattr(real_precheck, "probe_device", lambda *a, **kw: None)
    yield


def _fake_db():
    db = MagicMock()
    db.refresh = MagicMock()
    return db


def test_at_target_marks_install_complete(job, task):
    """The classic Retry-after-manual-finish case: device is on the new
    version but we never marked install_complete. Reconcile should set
    the marker so retry skips the install dance."""
    device = _device(current_version="11.1.4-h7", ha_state="passive")
    corrections = upgrade_svc.reconcile_markers_with_device_state(
        _fake_db(), job, task, device,
    )
    assert "install_complete" in task.progress["completed_phases"]
    assert "ensure_image" in task.progress["completed_phases"]
    assert any("install_complete" in c for c in corrections)


def test_install_marker_cleared_when_device_is_on_old_version(job, task):
    """The "marker lied" case: install_complete is set from a prior crash,
    but the device is actually still on the old version. Reconcile must
    clear the marker so retry redoes the install."""
    task.progress = {"completed_phases": ["install_complete"]}
    device = _device(current_version="10.2.0")
    upgrade_svc.reconcile_markers_with_device_state(
        _fake_db(), job, task, device,
    )
    assert "install_complete" not in task.progress["completed_phases"]


def test_ha_resume_complete_set_when_device_is_healthy_passive(job, task):
    """Inverse case: device is happily passive but ha_resume_complete
    isn't marked. Mark it so retry doesn't re-run the resume."""
    device = _device(ha_state="passive", ha_peer_id=2)
    upgrade_svc.reconcile_markers_with_device_state(
        _fake_db(), job, task, device,
    )
    assert "ha_resume_complete" in task.progress["completed_phases"]


def test_ha_resume_marker_cleared_when_device_is_suspended(job, task):
    """The user's hub1fw01 case: HA resume returned 200 OK and we marked
    it done, but PAN ignored the command and the device is still
    suspended. Reconcile clears the marker so retry re-runs resume."""
    task.progress = {"completed_phases": ["ha_resume_complete"]}
    device = _device(ha_state="suspended", ha_peer_id=2, current_version="11.1.4-h7")
    upgrade_svc.reconcile_markers_with_device_state(
        _fake_db(), job, task, device,
    )
    assert "ha_resume_complete" not in task.progress["completed_phases"]


def test_suspended_device_gets_suspend_ha_marker(job, task):
    """Reconciliation should observe an already-suspended device and mark
    suspend_ha done — saves a redundant suspend API call on retry."""
    device = _device(ha_state="suspended", current_version="10.2.0", ha_peer_id=2)
    upgrade_svc.reconcile_markers_with_device_state(
        _fake_db(), job, task, device,
    )
    assert "suspend_ha" in task.progress["completed_phases"]


def test_healthy_device_does_not_get_suspend_marker_cleared(job, task):
    """We deliberately do NOT clear a stale suspend_ha marker — clearing
    it would cause retry to RE-SUSPEND a healthy device. That'd be worse
    than the cosmetic issue of a redundant marker."""
    task.progress = {"completed_phases": ["suspend_ha"]}
    device = _device(ha_state="passive", ha_peer_id=2)
    upgrade_svc.reconcile_markers_with_device_state(
        _fake_db(), job, task, device,
    )
    assert "suspend_ha" in task.progress["completed_phases"]


def test_standalone_device_does_not_touch_ha_markers(job, task):
    """A device with no HA peer should never get HA-related markers
    set or cleared — they're meaningless for standalone."""
    device = _device(ha_state=None, ha_peer_id=None, current_version="11.1.4-h7")
    upgrade_svc.reconcile_markers_with_device_state(
        _fake_db(), job, task, device,
    )
    assert "ha_resume_complete" not in task.progress["completed_phases"]


def test_returns_empty_list_when_nothing_to_correct(job, task):
    """No-op case: a fresh task on a clean device returns no corrections
    so we don't log spurious 'reconciliation' entries."""
    device = _device(current_version="10.2.0", ha_state="passive", ha_peer_id=None)
    corrections = upgrade_svc.reconcile_markers_with_device_state(
        _fake_db(), job, task, device,
    )
    assert corrections == []
