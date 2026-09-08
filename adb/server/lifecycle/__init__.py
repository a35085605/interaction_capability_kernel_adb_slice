"""ADB server lifecycle contracts, acquisition, and recovery."""

from adb.server.lifecycle.errors import (
    AdbServerBootstrapError,
    AdbServerLifecycleConsistencyError,
    AdbServerLifecycleError,
)
from adb.server.lifecycle.backend import (
    AdbServerBackend,
    AdbServerBackendAcquired,
    AdbServerBackendAcquireDeferred,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireRevoked,
    AdbServerBackendAlreadyAcquired,
    AdbServerBackendAcquireResult,
    AdbServerBackendCleanupHandoff,
    AdbServerBackendCleanupHandoffError,
    AdbServerBackendFactory,
    AdbServerBackendReleased,
    AdbServerBackendReleaseInactive,
    AdbServerBackendReleaseMismatch,
    AdbServerBackendReleaseResult,
)
from adb.server.lifecycle.backend_template import AdbServerBackendEventPublisherBinding
from adb.server.lifecycle.backend import AdbServerBackendReleaseCleanupUnconfirmed
from adb.server.lifecycle.events import AdbServerActivated, AdbServerDeactivated

__all__ = [
    "AdbServerActivated",
    "AdbServerBackend",
    "AdbServerBackendAcquired",
    "AdbServerBackendAcquireDeferred",
    "AdbServerBackendAcquireFailed",
    "AdbServerBackendAcquireRevoked",
    "AdbServerBackendAlreadyAcquired",
    "AdbServerBackendAcquireResult",
    "AdbServerBackendCleanupHandoff",
    "AdbServerBackendCleanupHandoffError",
    "AdbServerBackendEventPublisherBinding",
    "AdbServerBackendFactory",
    "AdbServerBackendReleased",
    "AdbServerBackendReleaseInactive",
    "AdbServerBackendReleaseCleanupUnconfirmed",
    "AdbServerBackendReleaseMismatch",
    "AdbServerBackendReleaseResult",
    "AdbServerBootstrapError",
    "AdbServerDeactivated",
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleError",
]
