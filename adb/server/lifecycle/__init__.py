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
    AdbServerProvisionActivated,
    AdbServerProvisionActivationConflict,
    AdbServerProvisionOutcome,
    classify_provision_result,
)

__all__ = [
    "AdbServerAlreadyActive",
    "AdbServerAlreadyInactive",
    "AdbServerProvisionActivated",
    "AdbServerProvisionActivationConflict",
    "AdbServerProvisionOutcome",
    "AdbServerProvisionResult",
    "AdbServerRetireResult",
    "AdbServerBackend",
    "AdbServerLifecycleCoordinator",
    "AdbServerBootstrapError",
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleError",
    "classify_provision_result",
]
