"""SQLAlchemy ORM models. Importing this package registers everything with Base."""

from app.models.user import User  # noqa: F401
from app.models.credential import Credential  # noqa: F401
from app.models.panorama import Panorama  # noqa: F401
from app.models.device import Device  # noqa: F401
from app.models.image import PanosImage  # noqa: F401
from app.models.job import UpgradeJob, DeviceUpgradeTask  # noqa: F401
from app.models.precheck import BulkPrecheckRun, PrecheckRun  # noqa: F401
from app.models.stage import BulkStageRun, DeviceStageRun  # noqa: F401
from app.models.snapshot import Snapshot, SnapshotDiff, SnapshotKind  # noqa: F401
from app.models.precheck_set import PrecheckSet  # noqa: F401
from app.models.enums import (  # noqa: F401
    AuthType,
    CredentialScope,
    DeviceSource,
    HARole,
    JobState,
    Severity,
    TaskPhase,
    WorkflowType,
)
