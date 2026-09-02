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
    AdbServerProvisionEvidence,
    AdbServerProvisionResult,
    AdbServerRetireResult,
)

__all__ = [
    "AdbServerAlreadyActive",
    "AdbServerAlreadyInactive",
    "AdbServerProvisionEvidence",
    "AdbServerProvisionResult",
    "AdbServerRetireResult",
    "AdbServerBackend",
    "AdbServerLifecycleCoordinator",
    "AdbServerBootstrapError",
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleError",
]
