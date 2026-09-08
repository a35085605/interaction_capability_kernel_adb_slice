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
    AdbServerBackendFactory,
    AdbServerBackendPendingAcquireReleased,
    AdbServerBackendReleased,
    AdbServerBackendReleaseInactive,
    AdbServerBackendReleaseMismatch,
    AdbServerBackendReleaseResult,
)
from adb.server.lifecycle.backend_template import AdbServerBackendEventPublisherBinding
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
    "AdbServerBackendEventPublisherBinding",
    "AdbServerBackendFactory",
    "AdbServerBackendPendingAcquireReleased",
    "AdbServerBackendReleased",
    "AdbServerBackendReleaseInactive",
    "AdbServerBackendReleaseMismatch",
    "AdbServerBackendReleaseResult",
    "AdbServerBootstrapError",
    "AdbServerDeactivated",
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleError",
]
