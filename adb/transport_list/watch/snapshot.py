from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from _lifecycle_new.capability.lifecycle import LifecycleSnapshotReader
from _lifecycle_new.capability.snapshot import LifecyclePhase, LifecycleSnapshot
from adb.transport_list.watch.generation import AdbTransportListWatchGeneration
from adb.transport_list.watch.request import AdbTransportListWatchRequest
from adb.transport_list.watch.stream import AdbTransportListWatchStream


AdbTransportListWatchPhase: TypeAlias = LifecyclePhase

AdbTransportListWatchSnapshot: TypeAlias = LifecycleSnapshot[
    AdbTransportListWatchGeneration,
    AdbTransportListWatchRequest,
    AdbTransportListWatchStream,
]


@runtime_checkable
class AdbTransportListWatchSnapshotReader(
    LifecycleSnapshotReader[
        AdbTransportListWatchGeneration,
        AdbTransportListWatchRequest,
        AdbTransportListWatchStream,
    ],
    Protocol,
):
    """Read one consistent point-in-time watch lifecycle snapshot."""


__all__ = [
    "AdbTransportListWatchPhase",
    "AdbTransportListWatchSnapshot",
    "AdbTransportListWatchSnapshotReader",
]
