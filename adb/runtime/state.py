from __future__ import annotations

from dataclasses import dataclass

from adb.server.state import AdbServerStateStore
from adb.tracking.snapshot.state import AdbTransportListSnapshotState


@dataclass(frozen=True, slots=True)
class AdbRuntimeState:
    """Authoritative server and transport-list snapshot state owned by one ADB runtime."""

    server: AdbServerStateStore
    transport_list: AdbTransportListSnapshotState

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerStateStore):
            raise TypeError("server must be AdbServerStateStore")
        if not isinstance(self.transport_list, AdbTransportListSnapshotState):
            raise TypeError("transport_list must be AdbTransportListSnapshotState")


__all__ = ["AdbRuntimeState"]
