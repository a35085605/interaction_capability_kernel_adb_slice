from __future__ import annotations

from enum import Enum, auto
from typing import Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint


class AdbServerBackendPhase(Enum):
    """Backend-scoped lifecycle phase for one ADB server attachment."""

    IDLE = auto()
    ACQUIRING = auto()
    ACTIVE = auto()
    RELEASING = auto()


@runtime_checkable
class AdbServerBackend(Protocol):
    """Acquire and release one backend-scoped usable ADB server attachment.

    A backend defines the ownership semantics of the concrete resources required to provide the
    attachment.  Releasing an attachment relinquishes those backend resources; it does not imply
    that the underlying ADB server process itself is terminated.

    Implementations may use :class:`AdbServerBackendPhase` to coordinate their lifecycle, but the
    phase is intentionally not part of this command port.
    """

    def acquire(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerEndpoint:
        ...

    def release(self, endpoint: AdbServerEndpoint) -> None:
        ...


__all__ = ["AdbServerBackend", "AdbServerBackendPhase"]
