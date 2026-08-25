"""Typed failures exposed by the ADB server lifecycle control boundary."""


class AdbServerControlError(RuntimeError):
    """Base error for ADB server controller failures."""


class AdbServerStartError(AdbServerControlError):
    """A controller could not establish one fresh usable ADB server lifetime."""


class AdbServerStartDeferredError(AdbServerStartError):
    """Starting a fresh server is temporarily blocked by native lifecycle state."""


class AdbServerNativeLifetimeBusyError(AdbServerStartDeferredError):
    """A prior native server lifetime still occupies the backend slot."""


class AdbServerStopInProgressError(AdbServerStartDeferredError):
    """A prior native server lifetime is currently being terminated."""


class AdbServerStopError(AdbServerControlError):
    """A requested native-server stop operation could not be accepted or completed."""


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
