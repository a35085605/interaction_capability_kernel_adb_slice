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
    AdbServerBackendAcquireInterrupted,
    AdbServerBackendAlreadyAcquired,
    AdbServerBackendAcquireResult,
    AdbServerBackendFactory,
    AdbServerBackendReleased,
    AdbServerBackendReleaseInterruptedAcquire,
    AdbServerBackendReleaseMismatch,
    AdbServerBackendReleaseResult,
)
from adb.server.lifecycle.backend_template import (
    AdbServerBackendEventPublisherBinding,
    AdbServerBackendReleaseCleanupUnconfirmed,
)
from adb.server.lifecycle.events import AdbServerActivated, AdbServerDeactivated
from adb.server.lifecycle.coordinator import (
    AdbServerAlreadyActive,
    AdbServerAlreadyInactive,
    AdbServerLifecycleCoordinator,
    AdbServerProvisionResult,
    AdbServerRetireResult,
)
from adb.server.lifecycle.provision import (
    AdbServerProvisionActivated,
    AdbServerProvisionOutcome,
    classify_provision_result,
)

__all__ = [
    "AdbServerActivated",
    "AdbServerAlreadyActive",
    "AdbServerAlreadyInactive",
    "AdbServerBackend",
    "AdbServerBackendAcquired",
    "AdbServerBackendAcquireDeferred",
    "AdbServerBackendAcquireFailed",
    "AdbServerBackendAcquireInterrupted",
    "AdbServerBackendAlreadyAcquired",
    "AdbServerBackendAcquireResult",
    "AdbServerBackendEventPublisherBinding",
    "AdbServerBackendFactory",
    "AdbServerBackendReleased",
    "AdbServerBackendReleaseInterruptedAcquire",
    "AdbServerBackendReleaseCleanupUnconfirmed",
    "AdbServerBackendReleaseMismatch",
    "AdbServerBackendReleaseResult",
    "AdbServerBootstrapError",
    "AdbServerDeactivated",
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleCoordinator",
    "AdbServerLifecycleError",
    "AdbServerProvisionActivated",
    "AdbServerProvisionOutcome",
    "AdbServerProvisionResult",
    "AdbServerRetireResult",
    "classify_provision_result",
]
