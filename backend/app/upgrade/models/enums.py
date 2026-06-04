"""Enums used across the upgrade module's data model.

`DeviceSource` and `HARole` live in `core/devices/models/enums.py` —
they're shared with capacity polling. Anything here is upgrade-specific.
"""

import enum


class WorkflowType(str, enum.Enum):
    """Shape of a bulk upgrade run."""

    FULL = "full"          # precheck → snapshot → upgrade → postcheck → diff
    PARTIAL = "partial"    # user-defined subset; specifics stored on the job


class JobState(str, enum.Enum):
    """Top-level state of an UpgradeJob."""

    PENDING = "pending"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    # Terminal: every device reached a terminal state, but a mix of success and
    # failure — at least one device succeeded and at least one failed. A single
    # device's failure no longer fails the whole job (failure isolation).
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    ABORTED = "aborted"


class Severity(str, enum.Enum):
    """Three-state severity for pre-check results plus a 'skip' for not-applicable.

    Also reused on the staging table as the per-device outcome (PASS =
    already-downloaded, WARN = unused for staging, FAIL = error, SKIP =
    unused for staging). Reusing the enum avoids inventing a parallel
    StageOutcome enum that maps 1:1.
    """

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


class TaskPhase(str, enum.Enum):
    """Per-device state machine for an upgrade. HA pairs traverse these in sequence.

    Carry-forward from upgrader. The orchestrator's state transitions are
    bound to these names — preserve verbatim until phase 4c-services
    moves the orchestrator code in.
    """

    PENDING = "pending"
    PRECHECK = "precheck"
    AWAITING_PRECHECK_OVERRIDE = "awaiting_precheck_override"
    SNAPSHOT = "snapshot"
    DOWNLOADING_IMAGE = "downloading_image"
    # "Stage & hold" park: precheck + image + pre-snapshot done, paused before
    # install until the operator clicks "Proceed to install". Non-blocking gate
    # (drive_pair returns; the /confirm route re-dispatches), so it frees the
    # worker slot and doesn't count against the task time limit while parked.
    AWAITING_INSTALL_CONFIRM = "awaiting_install_confirm"
    SUSPEND_SECONDARY = "suspend_secondary"
    UPGRADE_SECONDARY = "upgrade_secondary"
    AWAITING_REBOOT_CONFIRM = "awaiting_reboot_confirm"
    POSTCHECK_SECONDARY = "postcheck_secondary"
    AWAITING_POSTCHECK_OVERRIDE = "awaiting_postcheck_override"
    AWAITING_FAILOVER_CONFIRM = "awaiting_failover_confirm"
    FAILOVER = "failover"
    AWAITING_PRIMARY_UPGRADE_CONFIRM = "awaiting_primary_upgrade_confirm"
    UPGRADE_PRIMARY = "upgrade_primary"
    POSTCHECK_PRIMARY = "postcheck_primary"
    FAILBACK = "failback"
    REPORT = "report"
    DONE = "done"
    FAILED = "failed"
