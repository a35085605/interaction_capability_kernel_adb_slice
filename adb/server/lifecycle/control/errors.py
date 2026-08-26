"""Typed domain failures exposed by the ADB server lifecycle control boundary."""


class AdbServerControlError(RuntimeError):
    """Base error for exceptional ADB server lifecycle-control failures."""


class AdbServerStopError(AdbServerControlError):
    """A requested server retirement or backend release could not be accepted or completed."""


class AdbServerBackendBusyError(AdbServerStopError):
    """Backend state temporarily prevents a requested server release."""


class AdbServerAttachmentMismatchError(AdbServerStopError):
    """A request targeted an endpoint other than the exact backend-owned attachment."""


__all__ = [
    "AdbServerAttachmentMismatchError",
    "AdbServerBackendBusyError",
    "AdbServerControlError",
    "AdbServerStopError",
]
