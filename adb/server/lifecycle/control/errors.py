"""Typed domain failures exposed by the ADB server lifecycle control boundary."""


class AdbServerControlError(RuntimeError):
    """Base error for ADB server lifecycle-control failures."""


class AdbServerStartError(AdbServerControlError):
    """A controller could not acquire one fresh usable ADB server attachment."""


class AdbServerStartDeferredError(AdbServerStartError):
    """Acquiring a fresh server attachment is temporarily blocked by current control state."""


class AdbServerStopError(AdbServerControlError):
    """A requested server-attachment release could not be accepted or completed."""


class AdbServerStopDeferredError(AdbServerStopError):
    """Releasing a server attachment is temporarily blocked by current control state."""


class AdbServerBackendBusyError(AdbServerStartDeferredError, AdbServerStopDeferredError):
    """Another backend operation prevents a request from beginning."""


class AdbServerNoAttachmentError(AdbServerStopError):
    """Release was requested while no server attachment is staged."""


class AdbServerAttachmentMismatchError(AdbServerStopError):
    """A request targeted an endpoint other than the exact backend-owned attachment."""


__all__ = [
    "AdbServerAttachmentMismatchError",
    "AdbServerBackendBusyError",
    "AdbServerControlError",
    "AdbServerNoAttachmentError",
    "AdbServerStartDeferredError",
    "AdbServerStartError",
    "AdbServerStopDeferredError",
    "AdbServerStopError",
]
