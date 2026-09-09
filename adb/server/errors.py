"""Typed exceptional failures exposed by the ADB server lifecycle boundary."""


class AdbServerLifecycleError(RuntimeError):
    """Base exceptional failure at the ADB server lifecycle boundary."""


class AdbServerLifecycleConsistencyError(AdbServerLifecycleError):
    """Lifecycle result and authoritative server state are inconsistent."""


__all__ = [
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleError",
]
