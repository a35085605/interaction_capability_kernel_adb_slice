from __future__ import annotations

from dataclasses import dataclass

from adb.server.state import (
    AdbServerState,
    AdbServerStateSnapshot,
    AdbServerStateTransition,
)
from adb.tracking.snapshot.state import AdbTransportListSnapshotState


@dataclass(frozen=True, slots=True)
class AdbRuntimeState:
    """Authoritative server and transport-list snapshot state owned by one ADB runtime."""

    server: AdbServerState
    transport_list: AdbTransportListSnapshotState

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerState):
            raise TypeError("server must be AdbServerState")
        if not isinstance(self.transport_list, AdbTransportListSnapshotState):
            raise TypeError("transport_list must be AdbTransportListSnapshotState")

    def observe_server(self) -> AdbServerStateSnapshot:
        """Capture the runtime-owned T0 server state for a lifecycle transaction."""

        return self.server.snapshot()

    def commit_server(self, transition: AdbServerStateTransition) -> bool:
        """Commit a T0 -> T1 server transition when T0 is still authoritative."""

        return self.server.commit(transition)


__all__ = ["AdbRuntimeState"]
