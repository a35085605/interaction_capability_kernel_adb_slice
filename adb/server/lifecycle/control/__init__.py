"""ADB server lifecycle control contracts, facade, and typed errors."""

from adb.server.lifecycle.control.controller import AdbServerController
from adb.server.lifecycle.control.errors import (
    AdbServerAcquireInProgressError,
    AdbServerAttachmentMismatchError,
    AdbServerControlError,
    AdbServerBackendBusyError,
    AdbServerNoAttachmentError,
    AdbServerStartDeferredError,
    AdbServerStartError,
    AdbServerStopDeferredError,
    AdbServerStopError,
    AdbServerStopInProgressError,
)
from adb.server.lifecycle.control.backend import (
    AdbServerBackend,
    AdbServerBackendLifecycle,
    AdbServerBackendPhase,
)

__all__ = [
    "AdbServerBackend",
    "AdbServerBackendLifecycle",
    "AdbServerBackendPhase",
    "AdbServerAcquireInProgressError",
    "AdbServerAttachmentMismatchError",
    "AdbServerControlError",
    "AdbServerController",
    "AdbServerBackendBusyError",
    "AdbServerNoAttachmentError",
    "AdbServerStartDeferredError",
    "AdbServerStartError",
    "AdbServerStopDeferredError",
    "AdbServerStopError",
    "AdbServerStopInProgressError",
]
