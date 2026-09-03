"""ADB server lifecycle contracts and bounded acquisition recovery."""

from adb.server.lifecycle.errors import (
    AdbServerBootstrapError,
    AdbServerLifecycleConsistencyError,
    AdbServerLifecycleError,
)
from adb.server.lifecycle.backend import AdbServerBackend
from adb.server.lifecycle.coordinator import (
    AdbServerAlreadyActive,
    AdbServerAlreadyInactive,
    AdbServerLifecycleCoordinator,
    AdbServerProvisionResult,
    AdbServerRetireResult,
)
from adb.server.lifecycle.provision import (
    AdbServerNonUsableAcquireResult,
    AdbServerProvisionActivated,
    AdbServerProvisionActivationConflict,
    AdbServerProvisionOutcome,
    AdbServerUsableAcquireResult,
    classify_provision_result,
)

__all__ = [
    "AdbServerAlreadyActive",
    "AdbServerAlreadyInactive",
    "AdbServerNonUsableAcquireResult",
    "AdbServerProvisionActivated",
    "AdbServerProvisionActivationConflict",
    "AdbServerProvisionOutcome",
    "AdbServerProvisionResult",
    "AdbServerUsableAcquireResult",
    "AdbServerRetireResult",
    "AdbServerBackend",
    "AdbServerLifecycleCoordinator",
    "AdbServerBootstrapError",
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleError",
    "classify_provision_result",
]
