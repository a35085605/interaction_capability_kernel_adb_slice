"""Typed exceptional failures exposed by the ADB server lifecycle control boundary."""


class AdbServerControlError(RuntimeError):
    """Exceptional ADB server lifecycle-control contract or consistency failure."""


__all__ = ["AdbServerControlError"]
