"""ADB server lifecycle contracts, endpoint access, results, and recovery."""

from adb._lifecycle import (
    AcquireBlocked,
    AcquireCommitted,
    AcquireExisting,
    AcquireFailed,
    AcquireSuperseded,
    ReleaseAccessDetached,
    ReleaseAcquisitionRevoked,
    ReleaseGenerationMismatch,
    ReleaseInactive,
)
from adb.server.lifecycle.errors import (
    AdbServerLifecycleConsistencyError,
    AdbServerLifecycleError,
)
from adb.server.lifecycle.contract import (
    AdbServerAccess,
    AdbServerAcquireOutcome,
    AdbServerLifecycle,
    AdbServerLifecycleFactory,
    AdbServerReleaseOutcome,
)

__all__ = [
    "AcquireBlocked",
    "AcquireCommitted",
    "AcquireExisting",
    "AcquireFailed",
    "AcquireSuperseded",
    "AdbServerAccess",
    "AdbServerAcquireOutcome",
    "AdbServerLifecycle",
    "AdbServerLifecycleFactory",
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleError",
    "AdbServerReleaseOutcome",
    "ReleaseAccessDetached",
    "ReleaseAcquisitionRevoked",
    "ReleaseGenerationMismatch",
    "ReleaseInactive",
]
