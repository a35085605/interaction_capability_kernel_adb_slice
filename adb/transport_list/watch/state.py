from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from adb.transport_list.watch.generation import AdbTransportListWatchGeneration
from adb.transport_list.watch.stream import AdbTransportListWatchStream


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchState:
    """Atomic watch generation/capability snapshot for the single stream consumer.

    ``capability is None`` means no watch stream is currently committed. Acquisition, draining, or
    cleanup work may still exist internally. Capturing this state does not lease the stream: a
    concurrent release may revoke its generation and retire the underlying resource immediately
    afterward. Repeated reads of one committed generation return the same single-consumer stream.
    """

    generation: AdbTransportListWatchGeneration
    capability: AdbTransportListWatchStream | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")
        if self.capability is not None and not isinstance(
            self.capability, AdbTransportListWatchStream
        ):
            raise TypeError("capability must satisfy AdbTransportListWatchStream or be None")


@runtime_checkable
class AdbTransportListWatchStateView(Protocol):
    """Read a linearizable snapshot of current watch authority and single-consumer stream."""

    def read(self) -> AdbTransportListWatchState:
        """Return one atomic generation/capability snapshot without extending its lifetime."""
        ...


__all__ = ["AdbTransportListWatchState", "AdbTransportListWatchStateView"]
