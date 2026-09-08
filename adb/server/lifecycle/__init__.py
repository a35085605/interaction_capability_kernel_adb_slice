"""ADB server lifecycle contracts, acquisition, and recovery."""

from adb.server.lifecycle.errors import (
    AdbServerBootstrapError,
    AdbServerLifecycleConsistencyError,
    AdbServerLifecycleError,
)
from adb.server.lifecycle.backend import (
    AdbServerLifecycle,
    AdbServerAcquisition,
    AdbServerBackendAcquireBlocked,
    AdbServerBackendAcquireCommitted,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireSuperseded,
    AdbServerBackendAcquireExisting,
    AdbServerAcquireOutcome,
    AdbServerLifecycleFactory,
    AdbServerBackendReleaseApplied,
    AdbServerBackendReleaseInactive,
    AdbServerBackendReleaseGenerationMismatch,
    AdbServerReleaseOutcome,
)
from adb.server.lifecycle.backend_template import AdbServerBackendEventPublisherBinding
from adb.server.lifecycle.events import AdbServerActivated, AdbServerDeactivated

__all__ = [
    "AdbServerActivated",
    "AdbServerLifecycle",
    "AdbServerAcquisition",
    "AdbServerBackendAcquireBlocked",
    "AdbServerBackendAcquireCommitted",
    "AdbServerBackendAcquireFailed",
    "AdbServerBackendAcquireSuperseded",
    "AdbServerBackendAcquireExisting",
    "AdbServerAcquireOutcome",
    "AdbServerBackendEventPublisherBinding",
    "AdbServerLifecycleFactory",
    "AdbServerBackendReleaseApplied",
    "AdbServerBackendReleaseInactive",
    "AdbServerBackendReleaseGenerationMismatch",
    "AdbServerReleaseOutcome",
    "AdbServerBootstrapError",
    "AdbServerDeactivated",
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleError",
]
