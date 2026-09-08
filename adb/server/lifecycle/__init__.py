"""ADB server lifecycle contracts, acquisition, and recovery."""

from adb.server.lifecycle.errors import (
    AdbServerBootstrapError,
    AdbServerLifecycleConsistencyError,
    AdbServerLifecycleError,
)
from adb.server.lifecycle.backend import (
    AdbServerBackend,
    AdbServerBackendAcquisition,
    AdbServerBackendAcquireBlocked,
    AdbServerBackendAcquireCommitted,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireSuperseded,
    AdbServerBackendAcquireExisting,
    AdbServerBackendAcquireOutcome,
    AdbServerBackendFactory,
    AdbServerBackendReleaseApplied,
    AdbServerBackendReleaseInactive,
    AdbServerBackendReleaseGenerationMismatch,
    AdbServerBackendReleaseOutcome,
)
from adb.server.lifecycle.backend_template import AdbServerBackendEventPublisherBinding
from adb.server.lifecycle.events import AdbServerActivated, AdbServerDeactivated

__all__ = [
    "AdbServerActivated",
    "AdbServerBackend",
    "AdbServerBackendAcquisition",
    "AdbServerBackendAcquireBlocked",
    "AdbServerBackendAcquireCommitted",
    "AdbServerBackendAcquireFailed",
    "AdbServerBackendAcquireSuperseded",
    "AdbServerBackendAcquireExisting",
    "AdbServerBackendAcquireOutcome",
    "AdbServerBackendEventPublisherBinding",
    "AdbServerBackendFactory",
    "AdbServerBackendReleaseApplied",
    "AdbServerBackendReleaseInactive",
    "AdbServerBackendReleaseGenerationMismatch",
    "AdbServerBackendReleaseOutcome",
    "AdbServerBootstrapError",
    "AdbServerDeactivated",
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleError",
]
