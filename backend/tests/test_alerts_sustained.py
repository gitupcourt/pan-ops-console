"""Sustained alerting + the resource-metric rule seed.

A rule with `sustained_samples > 1` opens an alert only after the threshold is
breached for that many CONSECUTIVE samples — so a one-poll CPU spike doesn't
flap an alert open. This covers the evaluator gate, the seeded CPU/memory rules
(migration 0018), and the rules-API round-trip of the new field.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.alerts.models.alert import Alert
from app.alerts.models.enums import AlertSeverity
from app.alerts.models.rule import AlertRule
from app.alerts.services.evaluator import evaluate_device_samples
from app.capacity.models.sample import Sample
from app.capacity.services.storage import SamplePoint
from app.core.devices.models.device import Device
from app.core.devices.models.enums import DeviceSource, HARole

STRONG = "correct horse battery staple"
T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
METRIC = "test_cpu"


def _signup(client):
    return client.post(
        "/auth/signup-first", json={"username": "admin", "password": STRONG}
    )


def _device(db) -> Device:
    dev = Device(
        name="fw-x",
        hostname="fw-x.local",
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


def _rule(db, *, severity, threshold, sustained):
    db.add(
        AlertRule(
            name=f"{METRIC} {severity.value}",
            metric=METRIC,
            severity=severity,
            threshold_pct=threshold,
            sustained_samples=sustained,
        )
    )
    db.commit()


def _sample(db, device_id, pct, ts):
    db.add(
        Sample(
            device_id=device_id, metric=METRIC, current_value=pct, max_value=100.0,
            pct=pct, ts=ts,
        )
    )
    db.commit()


def _point(device_id, pct, ts) -> SamplePoint:
    return SamplePoint(
        device_id=device_id, metric=METRIC, ts=ts, current=pct, max=100.0, pct=pct
    )


def _open(db, device_id):
    return (
        db.query(Alert)
        .filter(Alert.device_id == device_id, Alert.cleared_at.is_(None))
        .all()
    )


def _isolate(db):
    """Metric-specific rules so the global defaults (instantaneous) don't fire
    and mask the sustained behavior under test."""
    _rule(db, severity=AlertSeverity.WARNING, threshold=80, sustained=3)
    _rule(db, severity=AlertSeverity.CRITICAL, threshold=95, sustained=3)


def test_sustained_does_not_open_before_n(client, db):
    dev = _device(db)
    _isolate(db)
    _sample(db, dev.id, 85, T0)  # one prior breach
    evaluate_device_samples(db, dev.id, [_point(dev.id, 85, T0 + timedelta(seconds=60))])
    db.commit()
    assert _open(db, dev.id) == []  # only 2 of the 3 required → no alert yet


def test_sustained_opens_on_nth_consecutive(client, db):
    dev = _device(db)
    _isolate(db)
    _sample(db, dev.id, 85, T0)
    _sample(db, dev.id, 85, T0 + timedelta(seconds=60))
    evaluate_device_samples(
        db, dev.id, [_point(dev.id, 85, T0 + timedelta(seconds=120))]
    )
    db.commit()
    alerts = _open(db, dev.id)
    assert len(alerts) == 1
    assert alerts[0].severity == AlertSeverity.WARNING


def test_sustained_resets_on_a_dip(client, db):
    dev = _device(db)
    _isolate(db)
    _sample(db, dev.id, 50, T0)  # below threshold — breaks the streak
    _sample(db, dev.id, 85, T0 + timedelta(seconds=60))
    evaluate_device_samples(
        db, dev.id, [_point(dev.id, 85, T0 + timedelta(seconds=120))]
    )
    db.commit()
    assert _open(db, dev.id) == []  # 3 samples but not all breaching → no alert


def test_seeded_resource_rules_exist(client, db):
    """Migration 0018 seeds per-metric CPU/memory rules at sustained 3."""
    rule = (
        db.query(AlertRule)
        .filter(AlertRule.metric == "dp_cpu", AlertRule.severity == AlertSeverity.CRITICAL)
        .one()
    )
    assert rule.threshold_pct == 95
    assert rule.sustained_samples == 3


def test_rules_api_round_trips_sustained(client, db):
    _signup(client)
    r = client.post(
        "/alerts/rules",
        json={
            "name": "custom",
            "metric": "session_table_utilization",
            "severity": "warning",
            "threshold_pct": 70,
            "sustained_samples": 4,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["sustained_samples"] == 4
    rid = r.json()["id"]

    r = client.patch(f"/alerts/rules/{rid}", json={"sustained_samples": 2})
    assert r.status_code == 200, r.text
    assert r.json()["sustained_samples"] == 2
