"""Tests for the phase-8 capacity aggregation endpoints.

Covers the shapes + filter behavior of:
  - GET /capacity/heatmap
  - GET /capacity/table
  - GET /capacity/trend/{device_id}/{metric}

Plus the predicted-date math directly via `_predict_max_date`. Edge
cases there (insufficient data, decreasing trend, normal trend) are
the load-bearing logic — they decide whether an operator sees a
useful projection or a confusing one.

Integration coverage stays light: one seed device + two metrics is
enough to exercise filter combos and aggregation grouping. Heavy
fanout (50 devices × 17 metrics) is the territory of phase 15's
scale work — these tests don't try to exercise that.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.capacity.models.sample import Sample
from app.capacity.routes.aggregates import _predict_max_date
from app.core.devices.models.device import Device
from app.core.devices.models.enums import DeviceSource, HARole


STRONG = "correct horse battery staple"


def _signup(client):
    return client.post(
        "/auth/signup-first",
        json={"username": "admin", "password": STRONG},
    )


def _seed_device(db, *, name: str, model: str, version: str = "11.1.4-h7",
                 device_group: str | None = None,
                 template_stack: str | None = None) -> Device:
    dev = Device(
        name=name,
        hostname=f"{name}.local",
        model=model,
        current_version=version,
        device_group=device_group,
        template_stack=template_stack,
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


def _seed_sample(db, *, device_id: int, metric: str, ts: datetime,
                 current: float, max_value: float | None = 1000.0) -> None:
    pct = (current / max_value * 100.0) if max_value else None
    db.add(Sample(
        device_id=device_id, metric=metric, ts=ts,
        current_value=current, max_value=max_value, pct=pct,
    ))
    db.commit()


# ---------- predicted-date math ----------


def test_predict_insufficient_samples():
    """Fewer than 5 data points → won't extrapolate. The threshold
    avoids fitting a line to noise on a brand-new device."""
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    samples = [(t0 + timedelta(days=i), 100.0 * (i + 1)) for i in range(3)]
    pred = _predict_max_date(samples, max_value=1000.0)
    assert pred.method == "insufficient_data"
    assert pred.predicted_date is None


def test_predict_decreasing_trend():
    """Slope ≤ 0 → "decreasing", no projection. Operators care about
    'when will this hit max' — saying 'never' (or worse, projecting
    into the past) is wrong UX. Bail explicitly."""
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    samples = [
        (t0 + timedelta(days=i), 1000.0 - 50.0 * i) for i in range(10)
    ]
    pred = _predict_max_date(samples, max_value=2000.0)
    assert pred.method == "decreasing"
    assert pred.slope_per_day is not None and pred.slope_per_day < 0


def test_predict_normal_trend_projects_future_date():
    """Increasing trend with room to grow → date in the future,
    days_left > 0, slope_per_day > 0."""
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Linear: starts at 100, grows by 10/day. Max=1000 → hits at day 90.
    samples = [
        (t0 + timedelta(days=i), 100.0 + 10.0 * i) for i in range(30)
    ]
    pred = _predict_max_date(samples, max_value=1000.0)
    assert pred.method == "linear_regression"
    assert pred.slope_per_day is not None and pred.slope_per_day > 0
    assert pred.predicted_date is not None
    # We're at day 29 with value 390; need to hit 1000 → ~61 more days.
    assert 55 < (pred.days_left or 0) < 65


def test_predict_already_at_max():
    """Current ≥ max → 0 days left, projected date = latest sample. The
    UI should render this as 'past due' badge rather than projecting."""
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    samples = [
        (t0 + timedelta(days=i), 100.0 + 100.0 * i) for i in range(11)
    ]  # day 10 has value 1100, max=1000
    pred = _predict_max_date(samples, max_value=1000.0)
    assert pred.method == "linear_regression"
    assert pred.days_left == 0


def test_predict_no_max_value():
    """Unitless metric (no max) → no projection possible. Skip cleanly."""
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    samples = [(t0 + timedelta(days=i), 100.0 + i) for i in range(10)]
    pred = _predict_max_date(samples, max_value=None)
    assert pred.method == "insufficient_data"


# ---------- /capacity/heatmap ----------


def test_heatmap_groups_by_model_and_metric(client, db):
    _signup(client)
    pa220_a = _seed_device(db, name="A", model="PA-220")
    pa220_b = _seed_device(db, name="B", model="PA-220")
    pa820 = _seed_device(db, name="C", model="PA-820")

    now = datetime.now(timezone.utc)
    _seed_sample(db, device_id=pa220_a.id, metric="address_objects",
                 ts=now, current=900.0)  # 90%
    _seed_sample(db, device_id=pa220_b.id, metric="address_objects",
                 ts=now, current=500.0)  # 50%
    _seed_sample(db, device_id=pa820.id, metric="address_objects",
                 ts=now, current=100.0)  # 10%

    r = client.get("/capacity/heatmap")
    assert r.status_code == 200
    cells = r.json()
    by_model = {c["model"]: c for c in cells}
    assert "PA-220" in by_model
    assert "PA-820" in by_model
    # PA-220's tile reflects the highest device, even though one is at 50%.
    assert by_model["PA-220"]["max_pct"] == 90.0
    assert by_model["PA-220"]["device_count"] == 2
    # Top devices sorted by pct desc.
    top = by_model["PA-220"]["top_devices"]
    assert [d["device_name"] for d in top] == ["A", "B"]


def test_heatmap_filters_by_device_group(client, db):
    _signup(client)
    dg_a = _seed_device(db, name="A", model="PA-220", device_group="branches")
    dg_b = _seed_device(db, name="B", model="PA-220", device_group="hq")
    now = datetime.now(timezone.utc)
    _seed_sample(db, device_id=dg_a.id, metric="address_objects",
                 ts=now, current=950.0)
    _seed_sample(db, device_id=dg_b.id, metric="address_objects",
                 ts=now, current=100.0)

    r = client.get("/capacity/heatmap?device_group=branches")
    cells = r.json()
    # Only branches contributes — max_pct reflects only A.
    assert len(cells) == 1
    assert cells[0]["device_count"] == 1
    assert cells[0]["max_pct"] == 95.0


def test_heatmap_empty_when_no_samples(client, db):
    _signup(client)
    _seed_device(db, name="A", model="PA-220")
    r = client.get("/capacity/heatmap")
    assert r.status_code == 200
    assert r.json() == []


# ---------- /capacity/table ----------


def test_table_returns_latest_sample_per_device_metric(client, db):
    _signup(client)
    dev = _seed_device(db, name="A", model="PA-220")
    now = datetime.now(timezone.utc)
    # Older + newer for same (device, metric) — only newer should appear.
    _seed_sample(db, device_id=dev.id, metric="address_objects",
                 ts=now - timedelta(hours=2), current=500.0)
    _seed_sample(db, device_id=dev.id, metric="address_objects",
                 ts=now, current=900.0)

    r = client.get("/capacity/table")
    body = r.json()
    assert body["total"] == 1
    assert len(body["rows"]) == 1
    assert body["rows"][0]["current"] == 900.0


def test_table_row_exposes_serial_and_ip(client, db):
    """The table row carries serial + ip_address so the page's search box
    can match a device by the same identifiers as Inventory."""
    _signup(client)
    dev = _seed_device(db, name="A", model="PA-220")
    dev.serial = "0123456789"
    dev.ip_address = "10.9.8.7"
    db.commit()
    _seed_sample(
        db, device_id=dev.id, metric="address_objects",
        ts=datetime.now(timezone.utc), current=100.0,
    )

    r = client.get("/capacity/table")
    row = r.json()["rows"][0]
    assert row["serial"] == "0123456789"
    assert row["ip_address"] == "10.9.8.7"


def test_table_sorts_by_pct_desc(client, db):
    _signup(client)
    a = _seed_device(db, name="A", model="PA-220")
    b = _seed_device(db, name="B", model="PA-220")
    now = datetime.now(timezone.utc)
    _seed_sample(db, device_id=a.id, metric="address_objects",
                 ts=now, current=300.0)  # 30%
    _seed_sample(db, device_id=b.id, metric="address_objects",
                 ts=now, current=950.0)  # 95%

    r = client.get("/capacity/table")
    rows = r.json()["rows"]
    # Highest % first.
    assert rows[0]["device_name"] == "B"
    assert rows[1]["device_name"] == "A"


def test_table_filters_compose(client, db):
    _signup(client)
    a = _seed_device(db, name="A", model="PA-220", device_group="branches")
    b = _seed_device(db, name="B", model="PA-820", device_group="branches")
    c = _seed_device(db, name="C", model="PA-220", device_group="hq")
    now = datetime.now(timezone.utc)
    for d in (a, b, c):
        _seed_sample(db, device_id=d.id, metric="address_objects",
                     ts=now, current=500.0)

    # model=PA-220 + device_group=branches → only A.
    r = client.get("/capacity/table?model=PA-220&device_group=branches")
    rows = r.json()["rows"]
    assert [row["device_name"] for row in rows] == ["A"]


def test_table_pct_range_filter(client, db):
    _signup(client)
    a = _seed_device(db, name="A", model="PA-220")
    b = _seed_device(db, name="B", model="PA-220")
    now = datetime.now(timezone.utc)
    _seed_sample(db, device_id=a.id, metric="address_objects",
                 ts=now, current=950.0)  # 95%
    _seed_sample(db, device_id=b.id, metric="address_objects",
                 ts=now, current=300.0)  # 30%

    # Capacity Filter slider: only show rows in 80-100%.
    r = client.get("/capacity/table?pct_min=80")
    rows = r.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["device_name"] == "A"


# ---------- staleness (stopped-reporting devices) ----------


def test_heatmap_excludes_stale_device_from_color(client, db):
    """A device that stopped reporting keeps its last sample, but it must
    NOT drive the tile color — else a dead box glows. The fresh device sets
    max_pct; the stale one is still counted + flagged, just excluded from
    the max and sunk to the bottom of the hover list."""
    _signup(client)
    fresh = _seed_device(db, name="fresh", model="PA-220")
    stale = _seed_device(db, name="stale", model="PA-220")
    now = datetime.now(timezone.utc)
    _seed_sample(db, device_id=fresh.id, metric="address_objects",
                 ts=now, current=950.0)                         # 95%, live
    _seed_sample(db, device_id=stale.id, metric="address_objects",
                 ts=now - timedelta(days=60), current=990.0)    # 99%, frozen

    cell = client.get("/capacity/heatmap").json()[0]
    assert cell["device_count"] == 2
    assert cell["stale_count"] == 1
    # Color comes from the FRESH 95%, not the higher stale 99%.
    assert cell["max_pct"] == 95.0
    top = {d["device_name"]: d for d in cell["top_devices"]}
    assert top["stale"]["stale"] is True
    assert top["fresh"]["stale"] is False
    # Fresh sorts above the higher-pct stale device.
    assert cell["top_devices"][0]["device_name"] == "fresh"


def test_heatmap_all_stale_yields_no_color(client, db):
    """If every device in a bucket is stale, the tile has no live value —
    max_pct is None (renders as no-data, not a frozen hot color)."""
    _signup(client)
    d = _seed_device(db, name="A", model="PA-220")
    _seed_sample(db, device_id=d.id, metric="address_objects",
                 ts=datetime.now(timezone.utc) - timedelta(days=60),
                 current=990.0)
    cell = client.get("/capacity/heatmap").json()[0]
    assert cell["stale_count"] == 1
    assert cell["max_pct"] is None


def test_table_flags_stale_rows(client, db):
    """The table marks a stopped device's row stale so the UI can mute it
    and show 'last seen …' instead of trusting the frozen %."""
    _signup(client)
    fresh = _seed_device(db, name="fresh", model="PA-220")
    stale = _seed_device(db, name="stale", model="PA-220")
    now = datetime.now(timezone.utc)
    _seed_sample(db, device_id=fresh.id, metric="address_objects",
                 ts=now, current=100.0)
    _seed_sample(db, device_id=stale.id, metric="address_objects",
                 ts=now - timedelta(days=60), current=100.0)

    rows = {r["device_name"]: r
            for r in client.get("/capacity/table").json()["rows"]}
    assert rows["fresh"]["stale"] is False
    assert rows["stale"]["stale"] is True


def test_fresh_sample_is_never_stale(client, db):
    """Sanity guard against an inverted comparison or tz bug flagging live
    data: a just-written sample colors the tile and is not stale."""
    _signup(client)
    d = _seed_device(db, name="A", model="PA-220")
    _seed_sample(db, device_id=d.id, metric="address_objects",
                 ts=datetime.now(timezone.utc), current=500.0)
    cell = client.get("/capacity/heatmap").json()[0]
    assert cell["stale_count"] == 0
    assert cell["max_pct"] == 50.0


# ---------- /capacity/trend/{device_id}/{metric} ----------


def test_trend_returns_samples_and_prediction(client, db):
    _signup(client)
    dev = _seed_device(db, name="A", model="PA-220")
    t0 = datetime.now(timezone.utc) - timedelta(days=29)
    for i in range(30):
        _seed_sample(
            db, device_id=dev.id, metric="address_objects",
            ts=t0 + timedelta(days=i),
            current=100.0 + 10.0 * i,
        )

    r = client.get(f"/capacity/trend/{dev.id}/address_objects")
    body = r.json()
    assert body["device_name"] == "A"
    assert len(body["samples"]) == 30
    assert body["prediction"]["method"] == "linear_regression"
    assert body["prediction"]["slope_per_day"] > 0


def test_trend_unknown_device_404s(client):
    _signup(client)
    r = client.get("/capacity/trend/9999/address_objects")
    assert r.status_code == 404


def test_trend_empty_samples_returns_empty_prediction(client, db):
    _signup(client)
    dev = _seed_device(db, name="A", model="PA-220")
    r = client.get(f"/capacity/trend/{dev.id}/never_polled_metric")
    body = r.json()
    assert body["samples"] == []
    assert body["prediction"]["method"] == "insufficient_data"


# ---------- /upgrade/jobs/summary ----------


def test_jobs_summary_initial(client):
    _signup(client)
    r = client.get("/upgrade/jobs/summary")
    body = r.json()
    # Every JobState is present with 0 — shape-stable response.
    for state in ("pending", "running", "awaiting_confirmation",
                   "completed", "failed", "aborted"):
        assert body[state] == 0


# ---------- /devices/version-distribution ----------


def test_version_distribution_groups_by_current_version(client, db):
    _signup(client)
    _seed_device(db, name="A", model="PA-220", version="11.1.4-h7")
    _seed_device(db, name="B", model="PA-220", version="11.1.4-h7")
    _seed_device(db, name="C", model="PA-820", version="10.2.5")

    r = client.get("/devices/version-distribution")
    body = r.json()
    by_ver = {row["version"]: row["count"] for row in body}
    assert by_ver["11.1.4-h7"] == 2
    assert by_ver["10.2.5"] == 1


# ---------- /alerts (placeholder) ----------


def test_alerts_returns_empty_list_for_now(client):
    _signup(client)
    r = client.get("/alerts")
    assert r.status_code == 200
    assert r.json() == []
