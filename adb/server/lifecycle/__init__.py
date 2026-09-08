"""ADB server lifecycle contracts, acquisition, and recovery."""

from adb.server.lifecycle.errors import (
    AdbServerBootstrapError,
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
from adb.server.lifecycle.template import AdbServerLifecycleEventPublisherBinding
from adb.server.lifecycle.events import AdbServerActivated, AdbServerDeactivated

__all__ = [
    "AdbServerActivated",
    "AdbServerLifecycle",
    "AdbServerAcquisition",
    "AdbServerAcquireBlocked",
    "AdbServerAcquireCommitted",
    "AdbServerAcquireFailed",
    "AdbServerAcquireSuperseded",
    "AdbServerAcquireExisting",
    "AdbServerAcquireOutcome",
    "AdbServerLifecycleEventPublisherBinding",
    "AdbServerLifecycleFactory",
    "AdbServerReleaseApplied",
    "AdbServerReleaseInactive",
    "AdbServerReleaseGenerationMismatch",
    "AdbServerReleaseOutcome",
    "AdbServerBootstrapError",
    "AdbServerDeactivated",
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleError",
]
