from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from networking import TcpAddress

from adb.server.generation import AdbServerGeneration


@dataclass(frozen=True, slots=True)
class AdbServerState:
    """Atomic server generation/capability snapshot for data-plane consumers.

    ``capability is None`` means no server capability is currently committed. Acquisition,
    draining, or cleanup work may still exist internally. Capturing this state does not lease the
    capability: a concurrent release may revoke its generation immediately after ``read()``.
    """

    generation: AdbServerGeneration
    capability: TcpAddress | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")
        if self.capability is not None and not isinstance(self.capability, TcpAddress):
            raise TypeError("capability must be TcpAddress or None")


@runtime_checkable
class AdbServerStateView(Protocol):
    """Read a linearizable snapshot of current server authority and usable capability."""

    def read(self) -> AdbServerState:
        """Return one atomic generation/capability snapshot without extending its lifetime."""
        ...


__all__ = ["AdbServerState", "AdbServerStateView"]
