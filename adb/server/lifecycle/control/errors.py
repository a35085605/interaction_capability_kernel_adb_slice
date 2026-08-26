"""Typed domain failures exposed by the ADB server lifecycle control boundary."""


class AdbServerControlError(RuntimeError):
    """Base error for ADB server lifecycle-control failures."""


class AdbServerStartError(AdbServerControlError):
    """A controller could not acquire one fresh usable ADB server attachment."""


class AdbServerStartDeferredError(AdbServerStartError):
    """Acquiring a fresh server attachment is temporarily blocked by lifecycle state."""


class AdbServerStopError(AdbServerControlError):
    """A requested server-attachment release could not be accepted or completed."""


class AdbServerStopDeferredError(AdbServerStopError):
    """Releasing a server attachment is temporarily blocked by lifecycle state."""


class AdbServerBackendBusyError(AdbServerStartDeferredError):
    """A backend attachment is acquiring or active, so a fresh acquire cannot begin."""


class AdbServerAcquireInProgressError(AdbServerStopDeferredError):
    """Release was requested while the backend attachment is still acquiring."""


class AdbServerStopInProgressError(AdbServerStartDeferredError, AdbServerStopDeferredError):
    """A backend attachment is currently being released."""


class AdbServerNoAttachmentError(AdbServerStopError):
    """Release was requested while the backend owns no server attachment."""


class AdbServerAttachmentMismatchError(AdbServerStopError):
    """Release targeted an endpoint other than the exact backend-owned attachment."""


__all__ = [
    "AdbServerAcquireInProgressError",
    "AdbServerAttachmentMismatchError",
    "AdbServerBackendBusyError",
    "AdbServerControlError",
    "AdbServerNoAttachmentError",
    "AdbServerStartDeferredError",
    "AdbServerStartError",
    "AdbServerStopDeferredError",
    "AdbServerStopError",
    "AdbServerStopInProgressError",
]
