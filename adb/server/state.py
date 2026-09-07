from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from networking import TcpAddress

from adb.server.endpoint import AdbServerEndpoint
from adb.server.generation import AdbServerGeneration


@dataclass(frozen=True, slots=True)
class AdbServerState:
    """Atomic view of the current runtime-scoped ADB server authority.

    ``generation`` identifies the current authority lifetime. ``endpoint`` is present only
    while that generation owns a usable server endpoint. Pending acquisition, idle state,
    and post-revocation cleanup therefore all appear with ``endpoint`` set to ``None``.
    """

    generation: AdbServerGeneration
    endpoint: AdbServerEndpoint | None

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")
        if self.endpoint is not None and not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress or None")


@runtime_checkable
class AdbServerStateView(Protocol):
    """Read a linearizable snapshot of the current ADB server authority state."""

    def read(self) -> AdbServerState:
        """Return one atomic server-state snapshot."""
        ...


__all__ = ["AdbServerState", "AdbServerStateView"]
