"""Tests for DELETE /devices/{id}.

Regression focus: deleting a device that the operator can see in inventory
must actually work and must never fail silently.

Two real-world blockers were hit in the field deleting a factory-reset
device whose old serial lingered in inventory:

  1. `device_upgrade_tasks.device_id` was NO ACTION while every sibling
     device-scoped table cascades, so a device that had ever been in an
     upgrade job could not be deleted — the commit hit a FK violation, the
     API 500'd, and the UI swallowed it ("clicking Delete does nothing").
     Migration 0017 makes the task FK CASCADE; this pins it.

  2. The surviving member of an HA pair still points at the deleted device
     via the self-referential `ha_peer_id` (NO ACTION). The endpoint now
     releases that pointer before deleting, instead of failing.
"""

from __future__ import annotations

from app.core.devices.models.device import Device
from app.core.devices.models.enums import DeviceSource, HARole
from app.upgrade.models.job import DeviceUpgradeTask

STRONG = "correct horse battery staple"


def _signup(client):
    return client.post(
        "/auth/signup-first",
        json={"username": "admin", "password": STRONG},
    )


def _seed_standalone(db, *, name: str) -> Device:
    dev = Device(
        name=name,
        hostname=f"{name}.local",
        verify_tls=False,
        proxy_via_panorama=False,
        polling_enabled=True,
        source=DeviceSource.DIRECT,
        ha_role=HARole.STANDALONE,
    )
    db.add(dev)
    db.commit()
    db.refresh(dev)
    return dev


def _seed_ha_pair(db, *, name_a: str, name_b: str) -> tuple[Device, Device]:
    a = Device(
        name=name_a,
        hostname=f"{name_a}.local",
        verify_tls=False,
        proxy_via_panorama=False,
        polling_enabled=True,
        source=DeviceSource.DIRECT,
        ha_role=HARole.ACTIVE,
    )
    b = Device(
        name=name_b,
        hostname=f"{name_b}.local",
        verify_tls=False,
        proxy_via_panorama=False,
        polling_enabled=True,
        source=DeviceSource.DIRECT,
        ha_role=HARole.PASSIVE,
    )
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)
    a.ha_peer_id = b.id
    b.ha_peer_id = a.id
    db.commit()
    db.refresh(a)
    db.refresh(b)
    return a, b


def test_delete_plain_device(client, db):
    """Baseline: a device with no references deletes and returns 204."""
    _signup(client)
    dev = _seed_standalone(db, name="fw-plain")
    dev_id = dev.id

    r = client.delete(f"/devices/{dev_id}")
    assert r.status_code == 204, r.text

    db.expire_all()
    assert db.query(Device).filter_by(id=dev_id).count() == 0


def test_delete_missing_device_404(client, db):
    _signup(client)
    r = client.delete("/devices/99999")
    assert r.status_code == 404


def test_delete_device_with_upgrade_history_cascades(client, db):
    """A device that was part of an upgrade job must still be deletable; its
    task rows cascade out with it (migration 0017)."""
    _signup(client)
    dev = _seed_standalone(db, name="fw-old")
    dev_id = dev.id

    # Creating a job seeds DeviceUpgradeTask rows referencing the device.
    r = client.post(
        "/upgrade/jobs",
        json={
            "name": "history",
            "target_version": "11.1.4-h7",
            "device_ids": [dev_id],
            "device_pull_image": True,
        },
    )
    assert r.status_code == 201, r.text
    db.expire_all()
    assert (
        db.query(DeviceUpgradeTask).filter_by(device_id=dev_id).count() == 1
    )

    r = client.delete(f"/devices/{dev_id}")
    assert r.status_code == 204, r.text

    db.expire_all()
    assert db.query(Device).filter_by(id=dev_id).count() == 0
    # The task history cascaded out rather than blocking the delete.
    assert (
        db.query(DeviceUpgradeTask).filter_by(device_id=dev_id).count() == 0
    )


def test_delete_ha_member_releases_peer(client, db):
    """Deleting one HA member nulls the survivor's ha_peer_id (self-FK is
    NO ACTION) instead of failing, and leaves the survivor intact."""
    _signup(client)
    a, b = _seed_ha_pair(db, name_a="fw-a", name_b="fw-b")
    a_id, b_id = a.id, b.id

    r = client.delete(f"/devices/{a_id}")
    assert r.status_code == 204, r.text

    db.expire_all()
    assert db.query(Device).filter_by(id=a_id).count() == 0
    survivor = db.query(Device).filter_by(id=b_id).first()
    assert survivor is not None
    assert survivor.ha_peer_id is None
