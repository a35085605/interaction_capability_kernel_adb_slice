"""ADB server lifecycle contracts, acquisition, and recovery."""

from adb.server.lifecycle.errors import (
    AdbServerLifecycleConsistencyError,
    AdbServerLifecycleError,
)
from adb.server.lifecycle.contract import (
    AdbServerLifecycle,
    AdbServerAcquisition,
    AdbServerAcquireBlocked,
    AdbServerAcquireCommitted,
    AdbServerAcquireFailed,
    AdbServerAcquireSuperseded,
    AdbServerAcquireExisting,
    AdbServerAcquireOutcome,
    AdbServerLifecycleFactory,
    AdbServerReleaseApplied,
    AdbServerReleaseInactive,
    AdbServerReleaseGenerationMismatch,
    AdbServerReleaseOutcome,
)

__all__ = [
    "AdbServerLifecycle",
    "AdbServerAcquisition",
    "AdbServerAcquireBlocked",
    "AdbServerAcquireCommitted",
    "AdbServerAcquireFailed",
    "AdbServerAcquireSuperseded",
    "AdbServerAcquireExisting",
    "AdbServerAcquireOutcome",
    "AdbServerLifecycleFactory",
    "AdbServerReleaseApplied",
    "AdbServerReleaseInactive",
    "AdbServerReleaseGenerationMismatch",
    "AdbServerReleaseOutcome",
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleError",
]
