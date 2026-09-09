from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from adb._lifecycle import EndpointState
from adb.transport_list.watch.access import AdbTransportListWatchAccess
from adb.transport_list.watch.generation import AdbTransportListWatchGeneration


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchState(
    EndpointState[AdbTransportListWatchGeneration, AdbTransportListWatchAccess]
):
    """Watch-facing generation/access state with runtime validation."""

    def __post_init__(self) -> None:
        EndpointState.__post_init__(self)
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")
        if self.access is not None and not isinstance(
            self.access, AdbTransportListWatchAccess
        ):
            raise TypeError("access must be AdbTransportListWatchAccess or None")


@runtime_checkable
class AdbTransportListWatchStateView(Protocol):
    """Read a linearizable snapshot of current transport-list watch authority."""

    def read(self) -> AdbTransportListWatchState:
        """Return one atomic watch-state snapshot."""
        ...


__all__ = ["AdbTransportListWatchState", "AdbTransportListWatchStateView"]
