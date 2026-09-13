from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from _lifecycle_new.capability.snapshot import LifecyclePhase, LifecycleSnapshot
from adb.transport_list.watch.generation import AdbTransportListWatchGeneration
from adb.transport_list.watch.request import AdbTransportListWatchRequest
from adb.transport_list.watch.stream import AdbTransportListWatchStream


AdbTransportListWatchPhase: TypeAlias = LifecyclePhase

AdbTransportListWatchState: TypeAlias = LifecycleSnapshot[
    AdbTransportListWatchGeneration,
    AdbTransportListWatchRequest,
    AdbTransportListWatchStream,
]


@runtime_checkable
class AdbTransportListWatchStateView(Protocol):
    """Read one consistent point-in-time watch lifecycle snapshot."""

    def read(self) -> AdbTransportListWatchState:
        """Return the current generation, phase, request, and projected capability."""
        ...


__all__ = [
    "AdbTransportListWatchPhase",
    "AdbTransportListWatchState",
    "AdbTransportListWatchStateView",
]
