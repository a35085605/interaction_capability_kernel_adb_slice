"""ADB server lifecycle control contracts, facade, and typed errors."""

from adb.server.lifecycle.control.controller import AdbServerController
from adb.server.lifecycle.control.errors import (
    AdbServerAcquireInProgressError,
    AdbServerAttachmentMismatchError,
    AdbServerControlError,
    AdbServerNativeLifetimeBusyError,
    AdbServerNativeTerminationUnprovenError,
    AdbServerNoAttachmentError,
    AdbServerStartDeferredError,
    AdbServerStartError,
    AdbServerStopDeferredError,
    AdbServerStopError,
    AdbServerStopInProgressError,
)
from adb.server.lifecycle.control.backend import (
    AdbServerBackend,
    AdbServerBackendPhase,
    AdbServerBackendRequest,
)

__all__ = [
    "AdbServerBackend",
    "AdbServerBackendPhase",
    "AdbServerBackendRequest",
    "AdbServerAcquireInProgressError",
    "AdbServerAttachmentMismatchError",
    "AdbServerControlError",
    "AdbServerController",
    "AdbServerNativeLifetimeBusyError",
    "AdbServerNativeTerminationUnprovenError",
    "AdbServerNoAttachmentError",
    "AdbServerStartDeferredError",
    "AdbServerStartError",
    "AdbServerStopDeferredError",
    "AdbServerStopError",
    "AdbServerStopInProgressError",
]
