"""Typed failures exposed by the ADB server lifecycle control boundary."""


class AdbServerControlError(RuntimeError):
    """Base error for ADB server controller failures."""


class AdbServerStartError(AdbServerControlError):
    """A controller could not acquire one fresh usable ADB server attachment."""


class AdbServerStartDeferredError(AdbServerStartError):
    """Acquiring a fresh server attachment is temporarily blocked by backend lifecycle state."""


class AdbServerNativeLifetimeBusyError(AdbServerStartDeferredError):
    """A prior server attachment still occupies the backend slot."""


class AdbServerStopInProgressError(AdbServerStartDeferredError):
    """A prior server attachment is currently being released."""


class AdbServerStopError(AdbServerControlError):
    """A requested server-attachment release could not be accepted or completed."""


class AdbServerNativeTerminationUnprovenError(AdbServerStartError, AdbServerStopError):
    """Native termination cannot be proven and requires external intervention."""


__all__ = [
    "AdbServerControlError",
    "AdbServerNativeLifetimeBusyError",
    "AdbServerNativeTerminationUnprovenError",
    "AdbServerStartDeferredError",
    "AdbServerStartError",
    "AdbServerStopError",
    "AdbServerStopInProgressError",
]
