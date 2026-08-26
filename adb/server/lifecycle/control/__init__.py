"""ADB server lifecycle control contracts, facade, and typed errors."""

from adb.server.lifecycle.control.controller import AdbServerController
from adb.server.lifecycle.control.result import (
    AdbServerProvisionDeferred,
    AdbServerProvisionFailed,
    AdbServerProvisionResult,
    AdbServerProvisioned,
)
from adb.server.lifecycle.control.errors import (
    AdbServerAttachmentMismatchError,
    AdbServerControlError,
    AdbServerBackendBusyError,
    AdbServerStopError,
)
from adb.server.lifecycle.control.backend import AdbServerBackend

__all__ = [
    "AdbServerBackend",
    "AdbServerAttachmentMismatchError",
    "AdbServerControlError",
    "AdbServerController",
    "AdbServerProvisionDeferred",
    "AdbServerProvisionFailed",
    "AdbServerProvisionResult",
    "AdbServerProvisioned",
    "AdbServerBackendBusyError",
    "AdbServerStopError",
]
