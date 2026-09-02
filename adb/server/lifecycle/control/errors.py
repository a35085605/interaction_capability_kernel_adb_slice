"""Typed exceptional failures exposed by the ADB server lifecycle control boundary."""


class AdbServerLifecycleError(RuntimeError):
    """Base exceptional failure at the ADB server lifecycle boundary."""


class AdbServerBootstrapError(AdbServerLifecycleError):
    """Initial ADB server bootstrap could not establish a usable server."""


class AdbServerLifecycleConsistencyError(AdbServerLifecycleError):
    """Lifecycle result and authoritative server state are inconsistent."""


__all__ = [
    "AdbServerBootstrapError",
    "AdbServerLifecycleConsistencyError",
    "AdbServerLifecycleError",
]
