from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from adb._lifecycle import Snapshot
from adb.transport_list.watch.access import AdbTransportListWatchAccess
from adb.transport_list.watch.generation import AdbTransportListWatchGeneration
from adb.transport_list.watch.stream import AdbTransportListWatchStream


AdbTransportListWatchState: TypeAlias = Snapshot[
    AdbTransportListWatchGeneration,
    AdbTransportListWatchAccess,
    AdbTransportListWatchStream,
]


@runtime_checkable
class AdbTransportListWatchStateView(Protocol):
    """Read a linearizable snapshot of current watch authority and single-consumer stream."""

    def read(self) -> AdbTransportListWatchState:
        """Return one atomic generation/access/capability snapshot without leasing the stream."""
        ...


__all__ = ["AdbTransportListWatchState", "AdbTransportListWatchStateView"]
