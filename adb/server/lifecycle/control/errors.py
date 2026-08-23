"""Typed failures exposed by the ADB server lifecycle control boundary."""


class AdbServerControlError(RuntimeError):
    """Base error for ADB server controller failures."""


class AdbServerStartError(AdbServerControlError):
    """A controller could not establish one fresh usable ADB server lifetime."""


class AdbServerStopError(AdbServerControlError):
    """A controller could not prove the requested ADB server lifetime unavailable."""


__all__ = [
    "AdbServerControlError",
    "AdbServerStartError",
    "AdbServerStopError",
]
