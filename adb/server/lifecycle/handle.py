from __future__ import annotations

from typing import Protocol, runtime_checkable

from adb.server.control import AdbServerStopError
from adb.server.endpoint import AdbServerEndpoint


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


__all__ = [
    "AdbServerCloseError",
    "AdbServerProcessLifetime",
]
