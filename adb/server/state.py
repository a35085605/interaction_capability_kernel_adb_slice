from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from adb._lifecycle import EndpointState
from adb.server.generation import AdbServerGeneration


@dataclass(frozen=True, slots=True)
class AdbServerState(EndpointState[AdbServerGeneration]):
    """Server-facing endpoint state with server-generation runtime validation."""

    def __post_init__(self) -> None:
        EndpointState.__post_init__(self)
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")


@runtime_checkable
class AdbServerStateView(Protocol):
    """Read a linearizable snapshot of the current ADB server authority state."""

    def read(self) -> AdbServerState:
        """Return one atomic server-state snapshot."""
        ...


__all__ = ["AdbServerState", "AdbServerStateView"]
