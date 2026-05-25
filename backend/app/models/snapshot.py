"""Persisted firewall configuration / runtime snapshots.

A `Snapshot` is the result of pan-os-upgrade-assurance's `run_snapshots()` —
a dict keyed by "area" (e.g. `arp_table`, `routes`, `sessions`, `nics`,
`license`, `content_version`, ...) whose values are the device state at the
time of capture.

We persist these so we can render the same diff in the UI hours/days after
the upgrade ran, and so they're auditable. The orchestrator captures a
**pre-snapshot** before install and a **post-snapshot** after reboot
recovery; a `SnapshotDiff` row links the two plus the resulting compare
report.

Three reasons this lives in its own table (not just task.progress JSON):

  1. Size — snapshots can be tens of KB. Keeping them out of the per-task
     JSON keeps that hot row small and the orchestrator polling cheap.
  2. Lifecycle — snapshots may outlive their task (we want to keep the
     last-known-good snapshot per device even after the job is purged).
  3. Reuse — a future "ad-hoc snapshot" button on a device row writes to
     the same table without inventing a phony task.
"""

from datetime import datetime
import enum

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SnapshotKind(str, enum.Enum):
    """What this snapshot was taken for. Drives UI labeling and retention."""

    PRE_UPGRADE = "pre_upgrade"     # captured before install in an upgrade job
    POST_UPGRADE = "post_upgrade"   # captured after device is back from reboot
    AD_HOC = "ad_hoc"               # operator-initiated outside any job


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Linked to a specific upgrade task when captured during a job; null for ad-hoc.
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("device_upgrade_tasks.id", ondelete="SET NULL"), index=True, nullable=True
    )

    kind: Mapped[SnapshotKind] = mapped_column(
        Enum(SnapshotKind, name="snapshot_kind"), nullable=False
    )

    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )

    # Full snapshot blob: {area_name: {...device state...}}
    # JSON (not JSONB) for parity with the rest of the schema — we don't index
    # into it, only retrieve whole or compare whole.
    data: Mapped[dict] = mapped_column(JSON, nullable=False)

    # PAN-OS version at capture time. Lets the diff viewer show "10.2.0 → 11.1.4-h7".
    pan_os_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # If the snapshot capture itself failed (e.g. device offline), we still
    # write a row with empty `data` and the message here, so the orchestrator
    # timeline can show "snapshot skipped: reason".
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    device = relationship("Device", lazy="joined")


class SnapshotDiff(Base):
    """A `compare_snapshots()` report between two Snapshots.

    Stored separately from the snapshots themselves so the UI can render the
    diff without having to re-fetch and re-compare every time, and so a single
    pair of snapshots can have only one canonical diff.
    """

    __tablename__ = "snapshot_diffs"

    id: Mapped[int] = mapped_column(primary_key=True)
    left_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshots.id", ondelete="CASCADE"), index=True, nullable=False
    )
    right_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshots.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Convenience back-link so a job UI can list "diffs for this task" without
    # joining through snapshots.
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("device_upgrade_tasks.id", ondelete="SET NULL"), index=True, nullable=True
    )

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # The compare report from SnapshotCompare.compare_snapshots(). Shape:
    # {area: {"passed": bool, "added": [...], "missing": [...], "changed": {...}}}
    report: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Rolled-up flag so list endpoints don't have to inspect the report.
    all_passed: Mapped[bool] = mapped_column(default=True, nullable=False)
    # Comma-joined list of areas where passed=False; small enough for a column.
    failing_areas: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    left = relationship("Snapshot", foreign_keys=[left_snapshot_id], lazy="joined")
    right = relationship("Snapshot", foreign_keys=[right_snapshot_id], lazy="joined")
