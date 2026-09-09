from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from adb.server.access import AdbServerAccess
from adb.server.generation import AdbServerGeneration


@dataclass(frozen=True, slots=True)
class AdbServerState:
    """Server-facing generation/access state with runtime validation."""

    generation: AdbServerGeneration
    access: AdbServerAccess | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")
        if self.access is not None and not isinstance(self.access, AdbServerAccess):
            raise TypeError("access must be AdbServerAccess or None")


@runtime_checkable
class AdbServerStateView(Protocol):
    """Read a linearizable snapshot of the current ADB server authority state."""

    def read(self) -> AdbServerState:
        """Return one atomic server-state snapshot."""
        ...


__all__ = ["AdbServerState", "AdbServerStateView"]
