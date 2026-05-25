"""Enums used across the data model."""

import enum


class AuthType(str, enum.Enum):
    API_KEY = "api_key"
    USERPASS = "userpass"


class CredentialScope(str, enum.Enum):
    DEVICE = "device"
    PANORAMA = "panorama"


class DeviceSource(str, enum.Enum):
    DIRECT = "direct"            # added manually / CSV
    PANORAMA = "panorama"         # discovered via Panorama


class HARole(str, enum.Enum):
    STANDALONE = "standalone"
    ACTIVE = "active"
    PASSIVE = "passive"
    UNKNOWN = "unknown"


class WorkflowType(str, enum.Enum):
    FULL = "full"          # precheck → snapshot → upgrade → postcheck → diff
    PARTIAL = "partial"    # user-defined subset; specifics stored on the job


class JobState(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class Severity(str, enum.Enum):
    """Three-state severity for pre-check results plus a 'skip' for not-applicable."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


class TaskPhase(str, enum.Enum):
    """Per-device state machine for an upgrade. HA pairs traverse these in sequence."""

    PENDING = "pending"
    PRECHECK = "precheck"
    AWAITING_PRECHECK_OVERRIDE = "awaiting_precheck_override"  # gate after a precheck FAIL
    SNAPSHOT = "snapshot"
    DOWNLOADING_IMAGE = "downloading_image"
    SUSPEND_SECONDARY = "suspend_secondary"
    UPGRADE_SECONDARY = "upgrade_secondary"
    AWAITING_REBOOT_CONFIRM = "awaiting_reboot_confirm"  # install done, user must OK the reboot
    POSTCHECK_SECONDARY = "postcheck_secondary"
    AWAITING_POSTCHECK_OVERRIDE = "awaiting_postcheck_override"  # gate after a postcheck FAIL
    AWAITING_FAILOVER_CONFIRM = "awaiting_failover_confirm"
    FAILOVER = "failover"
    AWAITING_PRIMARY_UPGRADE_CONFIRM = "awaiting_primary_upgrade_confirm"
    UPGRADE_PRIMARY = "upgrade_primary"
    POSTCHECK_PRIMARY = "postcheck_primary"
    FAILBACK = "failback"
    REPORT = "report"
    DONE = "done"
    FAILED = "failed"
