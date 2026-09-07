from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from networking import TcpAddress

from adb.transport_list.watch.generation import AdbTransportListWatchGeneration


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchState:
    """Atomic view of current runtime-scoped transport-list watch authority.

    ``generation`` always identifies the current authority lifetime. ``endpoint`` is present
    only while that generation owns a usable resource session. Pending acquisition, idle state,
    and post-revocation cleanup expose no endpoint.
    """

    generation: AdbTransportListWatchGeneration
    endpoint: TcpAddress | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")
        if self.endpoint is not None and not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress or None")

    @property
    def active(self) -> bool:
        """Whether this generation currently owns a usable watch resource."""

        return self.endpoint is not None


@runtime_checkable
class AdbTransportListWatchStateView(Protocol):
    """Read a linearizable snapshot of current transport-list watch authority."""

    def read(self) -> AdbTransportListWatchState:
        """Return one atomic watch-state snapshot."""
        ...


__all__ = ["AdbTransportListWatchState", "AdbTransportListWatchStateView"]
