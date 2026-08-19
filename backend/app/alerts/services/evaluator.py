"""Alert evaluator — called per device after each successful poll.

Given the freshly-written samples for one device, compares each
sample's pct against the configured rules and either:
  - opens a new Alert row,
  - updates the existing open Alert's last_seen_at + snapshot fields
    (and severity if it escalated/de-escalated),
  - or clears the open Alert (with hysteresis to prevent flapping).

Per-poll cost is small: O(samples × 2) — for each sample, we look up
the rule for each severity. Rules are cached by metric for the
duration of a single evaluate() call.

Hysteresis: an alert clears only when pct drops at least
HYSTERESIS_PCT below the threshold that opened it. Without this,
metrics sitting right at the threshold (e.g. 75.1 → 74.9 → 75.0)
would flap-fire on every poll, generating noise.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.alerts.models.alert import Alert
from app.alerts.models.enums import AlertSeverity
from app.alerts.models.rule import AlertRule
from app.capacity.models.sample import Sample
from app.capacity.services.storage import SamplePoint

log = logging.getLogger(__name__)

# Severities in priority order — worst first. Used to find the
# "highest-severity firing rule" for each sample.
_SEVERITIES_BY_PRIORITY: tuple[AlertSeverity, ...] = (
    AlertSeverity.CRITICAL,
    AlertSeverity.WARNING,
)

# Drop the alert only when pct falls at least this far below the
# threshold that opened it. Five percentage points is enough to
# absorb sampling jitter without making "barely under" devices look
# healthy.
HYSTERESIS_PCT = 5.0


def evaluate_device_samples(
    db: Session, device_id: int, samples: list[SamplePoint]
) -> None:
    """Evaluate freshly-written samples against the active rule set.

    Idempotent in the sense that re-running with the same samples
    against the same DB state produces the same final DB state
    (active alerts updated in place, no duplicates).

    Caller is responsible for committing — we mutate the session but
    don't commit here, so the poller can batch the alert state with
    the existing per-device commit.
    """
    if not samples:
        return

    # Load all enabled rules once. The rule set is small (typically
    # 2 default rules + a handful of operator overrides), so loading
    # them all is cheaper than a per-sample query.
    rules = db.execute(
        select(AlertRule).where(AlertRule.enabled == True)  # noqa: E712
    ).scalars().all()
    rules_by_metric: dict[tuple[str | None, AlertSeverity], AlertRule] = {}
    for r in rules:
        rules_by_metric[(r.metric, r.severity)] = r

    def _rule_for(metric: str, severity: AlertSeverity) -> AlertRule | None:
        # Metric-specific override first; then the global default.
        return rules_by_metric.get((metric, severity)) or rules_by_metric.get(
            (None, severity)
        )

    # Pull all currently-open alerts for this device in one query so
    # the per-sample loop just does dict lookups.
    open_alerts = db.execute(
        select(Alert).where(
            and_(Alert.device_id == device_id, Alert.cleared_at.is_(None))
        )
    ).scalars().all()
    open_by_metric: dict[str, Alert] = {a.metric: a for a in open_alerts}

    now = datetime.now(timezone.utc)

    for s in samples:
        if s.pct is None:
            # No max means no pct means no threshold comparison
            # possible. Skip — these metrics will get alerts once
            # the catalog has a max extractor (or the platform's
            # `show system state` exposes one).
            continue

        # Find the highest-severity firing rule for this sample.
        firing: tuple[AlertSeverity, AlertRule] | None = None
        for sev in _SEVERITIES_BY_PRIORITY:
            rule = _rule_for(s.metric, sev)
            if rule and s.pct >= rule.threshold_pct:
                firing = (sev, rule)
                break

        existing = open_by_metric.get(s.metric)

        if firing is not None:
            sev, rule = firing
            if existing is not None:
                # Update in place — escalation/de-escalation between
                # warning and critical is recorded as a severity
                # change, not a new alert row.
                existing.severity = sev
                existing.threshold_pct = rule.threshold_pct
                existing.current_value = s.current
                existing.max_value = s.max
                existing.pct = s.pct
                existing.last_seen_at = now
            elif _breach_is_sustained(db, s, rule.threshold_pct, rule.sustained_samples):
                # OPEN — but only once the breach has persisted for the rule's
                # required consecutive samples (suppresses one-poll spikes on
                # volatile metrics like CPU). Escalating an already-open alert
                # is handled above and intentionally NOT re-gated.
                db.add(
                    Alert(
                        device_id=device_id,
                        metric=s.metric,
                        severity=sev,
                        threshold_pct=rule.threshold_pct,
                        current_value=s.current,
                        max_value=s.max,
                        pct=s.pct,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                )
                log.info(
                    "Alert opened: device=%s metric=%s severity=%s pct=%.1f "
                    "threshold=%d (sustained %d)",
                    device_id,
                    s.metric,
                    sev.value,
                    s.pct,
                    rule.threshold_pct,
                    rule.sustained_samples,
                )
        else:
            # No rule is firing for this sample. If we have an open
            # alert and the value is far enough below the threshold,
            # clear it.
            if existing is not None and s.pct < existing.threshold_pct - HYSTERESIS_PCT:
                existing.cleared_at = now
                log.info(
                    "Alert cleared: device=%s metric=%s pct=%.1f (was severity=%s)",
                    device_id,
                    s.metric,
                    s.pct,
                    existing.severity.value,
                )


def _breach_is_sustained(
    db: Session, sample: SamplePoint, threshold_pct: int, sustained_samples: int
) -> bool:
    """True if this sample plus the prior ``sustained_samples - 1`` for the same
    (device, metric) are ALL at/above the threshold.

    ``sustained_samples <= 1`` is instantaneous (always True) — the original
    behavior, right for slow-moving config counts. Higher values gate OPENING an
    alert so a one-poll spike on a volatile metric (CPU) doesn't flap. The
    current sample's pct is used directly (already in hand); the earlier ones
    come from the persisted history.
    """
    if sustained_samples <= 1:
        return True
    prior = db.execute(
        select(Sample.pct)
        .where(
            Sample.device_id == sample.device_id,
            Sample.metric == sample.metric,
            Sample.ts < sample.ts,
        )
        .order_by(Sample.ts.desc())
        .limit(sustained_samples - 1)
    ).scalars().all()
    pcts = [sample.pct, *prior]
    return len(pcts) >= sustained_samples and all(
        p is not None and p >= threshold_pct for p in pcts
    )
