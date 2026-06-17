"""Tests for POST /devices/{id}/replace-serial (RMA serial replacement).

When a firewall is RMA'd it comes back with a new serial, and Panorama sync
auto-imports that as a fresh record. Replace-serial re-points the original
record to the new serial so the one record carries forward across the swap:
identity + config + history stay, the new box's runtime state is carried over
from the auto-imported duplicate, the duplicate is absorbed, and the stale API
key is cleared.
"""

from __future__ import annotations

from app.capacity.models.sample import Sample
from app.core.devices.models.device import Device
from app.core.devices.models.enums import DeviceSource, HARole
from app.upgrade.models.job import DeviceUpgradeTask

STRONG = "correct horse battery staple"


def _signup(client):
    return client.post(
        "/auth/signup-first",
        json={"username": "admin", "password": STRONG},
    )


def _seed_device(db, *, name, serial, api_key=b"key", **extra) -> Device:
    dev = Device(
        name=name,
        hostname=f"{name}.local",
        serial=serial,
        verify_tls=False,
        proxy_via_panorama=False,
        polling_enabled=True,
        source=DeviceSource.DIRECT,
        ha_role=HARole.STANDALONE,
        encrypted_api_key=api_key,
        **extra,
    )
    db.add(dev)
    db.commit()
    db.refresh(dev)
    return dev


def _seed_sample(db, device_id, metric="session"):
    db.add(Sample(device_id=device_id, metric=metric, current_value=1.0))
    db.commit()


def test_replace_serial_no_duplicate(client, db):
    """New serial not yet in inventory: just swap it, keep history, clear key."""
    _signup(client)
    dev = _seed_device(db, name="solo-fw", serial="OLD000000010", api_key=b"k")
    dev_id = dev.id
    _seed_sample(db, dev_id)

    r = client.post(
        f"/devices/{dev_id}/replace-serial",
        json={"new_serial": "FRESH00000099"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["serial"] == "FRESH00000099"
    assert r.json()["has_api_key"] is False  # creds cleared for the new box

    db.expire_all()
    kept = db.query(Device).filter_by(id=dev_id).first()
    assert kept.serial == "FRESH00000099"
    assert kept.encrypted_api_key is None
    # History stays attached to the same record id.
    assert db.query(Sample).filter_by(device_id=dev_id).count() == 1


def test_replace_serial_absorbs_duplicate(client, db):
    """The real RMA case: the new serial was already auto-imported. The kept
    record adopts the serial + the new box's runtime state, the duplicate's
    post-import history is re-pointed here, and the duplicate is removed."""
    _signup(client)
    target = _seed_device(
        db, name="branch-fw", serial="OLD000000001", api_key=b"oldkey"
    )
    target_id = target.id
    # Target carries real history: a capacity sample + an upgrade task.
    _seed_sample(db, target_id)
    r = client.post(
        "/upgrade/jobs",
        json={
            "name": "hist",
            "target_version": "11.1.4-h7",
            "device_ids": [target_id],
            "device_pull_image": True,
        },
    )
    assert r.status_code == 201, r.text

    # The replacement box auto-imported under a new serial, with fresh state.
    dup = _seed_device(
        db,
        name="NEW000000002",
        serial="NEW000000002",
        api_key=None,
        current_version="11.2.0",
        model="PA-VM",
    )
    dup_id = dup.id
    _seed_sample(db, dup_id)  # a post-import sample on the new box

    r = client.post(
        f"/devices/{target_id}/replace-serial",
        json={"new_serial": "NEW000000002"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["serial"] == "NEW000000002"
    assert body["model"] == "PA-VM"  # runtime carried from the new box
    assert body["has_api_key"] is False  # creds cleared

    db.expire_all()
    # Duplicate absorbed.
    assert db.query(Device).filter_by(id=dup_id).count() == 0
    kept = db.query(Device).filter_by(id=target_id).first()
    assert kept.serial == "NEW000000002"
    assert kept.current_version == "11.2.0"  # carried from the duplicate
    assert kept.model == "PA-VM"
    assert kept.encrypted_api_key is None
    # The kept record's own history survived AND the duplicate's post-import
    # sample was re-pointed here (2 samples now), upgrade task intact.
    assert db.query(Sample).filter_by(device_id=target_id).count() == 2
    assert (
        db.query(DeviceUpgradeTask).filter_by(device_id=target_id).count() == 1
    )


def test_replace_serial_same_serial_rejected(client, db):
    _signup(client)
    dev = _seed_device(db, name="fw-x", serial="SAME0000001", api_key=b"k")
    r = client.post(
        f"/devices/{dev.id}/replace-serial",
        json={"new_serial": "SAME0000001"},
    )
    assert r.status_code == 400
    assert "already has this serial" in r.text.lower()


def test_replace_serial_blank_rejected(client, db):
    _signup(client)
    dev = _seed_device(db, name="fw-b", serial="OLD22222", api_key=b"k")
    r = client.post(
        f"/devices/{dev.id}/replace-serial",
        json={"new_serial": "   "},
    )
    assert r.status_code == 422


def test_replace_serial_missing_device_404(client, db):
    _signup(client)
    r = client.post(
        "/devices/99999/replace-serial", json={"new_serial": "X12345"}
    )
    assert r.status_code == 404
