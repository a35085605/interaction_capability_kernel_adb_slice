"""ADB server lifecycle contracts and bounded acquisition recovery."""

from adb.server.lifecycle.errors import (
    AdbServerBootstrapError,
    AdbServerLifecycleConsistencyError,
    AdbServerLifecycleError,
)
from adb.server.lifecycle.backend import AdbServerBackend
from adb.server.lifecycle.coordinator import (
    AdbServerAcquireOnceResult,
    AdbServerLifecycleCoordinator,
)

__all__ = [
    "AdbServerAcquireOnceResult",
    "AdbServerBackend",
    "AdbServerLifecycleCoordinator",
    "AdbServerBootstrapError",
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleError",
]
