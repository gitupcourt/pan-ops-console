"""HA self-heal: a failed / aborted / crashed upgrade must never strand a
cluster degraded on a single node (the job-10 -> job-11 trap).

We suspend the passive member to install it. If the drive then ends *between*
that suspend and the normal resume — a phase failure, a worker crash, or an
operator abort — the member is left 'suspended': the cluster is running on one
node with no redundancy, AND the next job can't classify the pair (a suspended
member reads role 'unknown'), so it aborts with "Could not determine HA roles".

Two layers fix this:
  - Layer 1 `_heal_suspended_members` — on every drive that ends without
    completing (entry-bail on a terminal job, post-drive on FAILED/ABORTED,
    crash handler) resume any member we left suspended.
  - Layer 2 `_heal_suspended_for_classify` — at the *start* of a fresh pair
    drive, if the pair won't classify because a member is stuck suspended from
    an earlier interrupted run, resume + re-probe so it becomes classifiable.

Both lean on `_resume_suspended_member`, which is strictly best-effort: it never
raises (healing must not mask the original failure) and confirms the member
actually left 'suspended' before claiming success.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.devices.models.enums import HARole
from app.upgrade.services import upgrade


def _device(*, ha_peer_id=2, ha_state="suspended", ha_role=HARole.UNKNOWN, name="fw01"):
    return SimpleNamespace(
        id=1, name=name, ha_peer_id=ha_peer_id, ha_state=ha_state, ha_role=ha_role
    )


def _task(device=None, progress=None, tid=1):
    return SimpleNamespace(
        id=tid,
        device=device,
        device_id=getattr(device, "id", tid),
        progress=dict(progress or {}),
        phase=upgrade.TaskPhase.UPGRADE_PRIMARY,
        error=None,
    )


def _log(task) -> str:
    return " ".join(task.progress.get("log", []))


# ---------- _resume_suspended_member ----------


def test_resume_skips_standalone(monkeypatch):
    calls = []
    monkeypatch.setattr(upgrade, "_ha_op_with_retry", lambda *a, **k: calls.append(a) or True)
    dev = _device(ha_peer_id=None, ha_state=None)
    assert upgrade._resume_suspended_member(MagicMock(), _task(dev), dev) is True
    assert calls == []  # no HA op issued for a device with no peer


def test_resume_skips_already_healthy(monkeypatch):
    calls = []
    monkeypatch.setattr(upgrade, "_ha_op_with_retry", lambda *a, **k: calls.append(a) or True)
    dev = _device(ha_state="passive")  # not suspended — nothing to heal
    assert upgrade._resume_suspended_member(MagicMock(), _task(dev), dev) is True
    assert calls == []


def test_resume_returns_false_when_command_fails(monkeypatch):
    monkeypatch.setattr(upgrade, "_ha_op_with_retry", lambda *a, **k: False)
    verify_calls = []
    monkeypatch.setattr(
        upgrade, "_verify_resume_took_effect", lambda *a, **k: verify_calls.append(1) or True
    )
    dev = _device()
    task = _task(dev)
    assert upgrade._resume_suspended_member(MagicMock(), task, dev) is False
    assert verify_calls == []  # never verifies if the resume command itself failed
    log = _log(task)
    assert "Could not resume HA" in log
    assert "request high-availability state functional" in log  # manual-recovery hint


def test_resume_returns_false_when_verify_fails(monkeypatch):
    monkeypatch.setattr(upgrade, "_ha_op_with_retry", lambda *a, **k: True)
    monkeypatch.setattr(upgrade, "_verify_resume_took_effect", lambda *a, **k: False)
    dev = _device()
    task = _task(dev)
    assert upgrade._resume_suspended_member(MagicMock(), task, dev) is False
    assert "stayed suspended" in _log(task)


def test_resume_succeeds(monkeypatch):
    monkeypatch.setattr(upgrade, "_ha_op_with_retry", lambda *a, **k: True)
    monkeypatch.setattr(upgrade, "_verify_resume_took_effect", lambda *a, **k: True)
    dev = _device()
    task = _task(dev)
    assert upgrade._resume_suspended_member(MagicMock(), task, dev) is True
    assert "HA resumed" in _log(task)


def test_resume_never_raises(monkeypatch):
    # Healing is best-effort; an error in the resume path must be swallowed so it
    # never masks the original phase failure that triggered the heal.
    def boom(*a, **k):
        raise RuntimeError("HA daemon unreachable")

    monkeypatch.setattr(upgrade, "_ha_op_with_retry", boom)
    dev = _device()
    task = _task(dev)
    assert upgrade._resume_suspended_member(MagicMock(), task, dev) is False
    assert "HA resume errored" in _log(task)


# ---------- _heal_suspended_members (Layer 1) ----------


def test_heal_resumes_suspended_member_despite_stale_marker(monkeypatch):
    """The heal trusts live HA state, NOT the ha_resume_complete marker. reconcile
    sets that marker at entry when the pair starts healthy, but THIS upgrade then
    suspends the member to install it — so a stale marker must not make us skip
    releasing it (job-17 regression: a failed install left the secondary
    suspended). _resume_suspended_member is a no-op on a member that isn't
    suspended (see test_resume_skips_already_healthy), so calling it for every
    task is safe."""
    resumed = []
    monkeypatch.setattr(
        upgrade, "_resume_suspended_member", lambda db, t, d: resumed.append(t.id) or True
    )
    marked = _task(_device(), progress={"completed_phases": ["ha_resume_complete"]}, tid=1)
    pending = _task(_device(), tid=2)
    upgrade._heal_suspended_members(MagicMock(), SimpleNamespace(), [marked, pending])
    assert resumed == [1, 2]  # BOTH attempted — the stale marker no longer skips


def test_heal_resumes_each_unfinished_member(monkeypatch):
    resumed = []
    monkeypatch.setattr(
        upgrade, "_resume_suspended_member", lambda db, t, d: resumed.append(t.id) or True
    )
    tasks = [_task(_device(), tid=1), _task(_device(), tid=2)]
    upgrade._heal_suspended_members(MagicMock(), SimpleNamespace(), tasks)
    assert resumed == [1, 2]


# ---------- _heal_suspended_for_classify (Layer 2) ----------


def test_classify_heal_noop_when_none_suspended(monkeypatch):
    resume_calls, probe_calls = [], []
    monkeypatch.setattr(
        upgrade, "_resume_suspended_member", lambda *a, **k: resume_calls.append(1) or True
    )
    monkeypatch.setattr(upgrade.precheck_svc, "probe_device", lambda *a, **k: probe_calls.append(1))
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: MagicMock())
    tasks = [_task(_device(ha_state="passive")), _task(_device(ha_state="active"))]
    assert upgrade._heal_suspended_for_classify(MagicMock(), SimpleNamespace(), tasks) is False
    assert resume_calls == []  # nothing suspended -> nothing resumed
    assert probe_calls == []  # and no re-probe if no resume was attempted


def test_classify_heal_skips_standalone(monkeypatch):
    resume_calls = []
    monkeypatch.setattr(
        upgrade, "_resume_suspended_member", lambda *a, **k: resume_calls.append(1) or True
    )
    monkeypatch.setattr(upgrade.precheck_svc, "probe_device", lambda *a, **k: None)
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: MagicMock())
    standalone = _task(_device(ha_peer_id=None, ha_state="suspended"))
    assert upgrade._heal_suspended_for_classify(MagicMock(), SimpleNamespace(), [standalone]) is False
    assert resume_calls == []  # no peer -> not a degraded cluster, leave it


def test_classify_heal_resumes_then_reprobes(monkeypatch):
    monkeypatch.setattr(upgrade, "_resume_suspended_member", lambda *a, **k: True)
    probed = []
    monkeypatch.setattr(
        upgrade.precheck_svc, "probe_device", lambda db, dev, client=None: probed.append(dev.name)
    )
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: MagicMock())
    susp = _task(_device(ha_state="suspended", name="branch1fw01"), tid=1)
    peer = _task(_device(ha_state="active", name="branch1fw02"), tid=2)
    assert upgrade._heal_suspended_for_classify(MagicMock(), SimpleNamespace(), [susp, peer]) is True
    # re-probe runs for BOTH members so the caller's re-classify reads settled roles
    assert sorted(probed) == ["branch1fw01", "branch1fw02"]
    assert "suspended" in _log(susp).lower()


# ---------- end-to-end: the job-10 -> job-11 recovery ----------


def test_suspended_member_recovers_classification(monkeypatch):
    """A pair left with one suspended member won't classify (the live role reads
    UNKNOWN). After Layer 2 resumes it and re-probes, a re-classify succeeds —
    exactly how a retried branch1/hub1 pair self-heals."""
    db = MagicMock()
    susp = _task(_device(ha_state="suspended", ha_role=HARole.UNKNOWN, name="branch1fw01"), tid=1)
    peer = _task(_device(ha_state="active", ha_role=HARole.UNKNOWN, name="branch1fw02"), tid=2)

    # 1) As left by the interrupted job: not classifiable -> caller would abort.
    passive, active = upgrade._classify_pair(db, [susp, peer])
    assert passive is None  # "Could not determine HA roles" territory

    # 2) Heal: resume the suspended member, then the re-probe settles both roles.
    def fake_resume(db_, task, device):
        if (device.ha_state or "").lower() == "suspended":
            device.ha_state = "passive"  # rejoined the cluster
        return True

    def fake_probe(db_, device, client=None):
        st = (device.ha_state or "").lower()
        device.ha_role = HARole.PASSIVE if st == "passive" else HARole.ACTIVE

    monkeypatch.setattr(upgrade, "_resume_suspended_member", fake_resume)
    monkeypatch.setattr(upgrade.precheck_svc, "probe_device", fake_probe)
    monkeypatch.setattr(upgrade, "_client_for", lambda db_, d: MagicMock())

    assert upgrade._heal_suspended_for_classify(db, SimpleNamespace(), [susp, peer]) is True

    # 3) Re-classify now succeeds — the pair is drivable again.
    passive, active = upgrade._classify_pair(db, [susp, peer])
    assert passive is susp and active is peer
