"""ADB server backend control contracts and typed errors."""

from adb.server.lifecycle.control.result import (
    AdbServerProvisionDeferred,
    AdbServerProvisionFailed,
)
from adb.server.lifecycle.control.errors import (
    AdbServerBootstrapError,
    AdbServerLifecycleConsistencyError,
    AdbServerLifecycleError,
)
from adb.server.lifecycle.control.backend import AdbServerBackend

__all__ = [
    "AdbServerBackend",
    "AdbServerBootstrapError",
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleError",
    "AdbServerProvisionDeferred",
    "AdbServerProvisionFailed",
]
