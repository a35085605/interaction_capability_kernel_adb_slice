from __future__ import annotations

from typing import Protocol, runtime_checkable

from adb.server.control import AdbServerControlError, AdbServerStopError
from adb.server.endpoint import AdbServerEndpoint


AdbServerNativeError = AdbServerControlError


class AdbServerCloseError(AdbServerStopError):
    """Compatibility error for unproven exact process termination."""


@runtime_checkable
class AdbServerProcessLifetime(Protocol):
    """Backend-only capability for one exact native ADB server process lifetime.

    This is intentionally a process-backend concept, not ADB-level ownership.  ADB domain
    code should retain only server identity and creation provenance; concrete launch backends
    may keep this capability privately to provide stronger teardown guarantees than
    ``adb kill-server`` can provide.
    """

    @property
    def endpoint(self) -> AdbServerEndpoint: ...

    @property
    def active(self) -> bool: ...

    def close(self) -> None:
        """Terminate this exact native lifetime, or raise when termination is not proven."""
        ...


# Compatibility alias for callers of the former ADB-facing name.  The capability is now
# explicitly documented as a process-backend implementation detail.
AdbServerNativeHandle = AdbServerProcessLifetime


__all__ = [
    "AdbServerCloseError",
    "AdbServerNativeError",
    "AdbServerNativeHandle",
    "AdbServerProcessLifetime",
]
