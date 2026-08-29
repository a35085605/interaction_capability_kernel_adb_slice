from __future__ import annotations

from dataclasses import dataclass

from adb.server.state import (
    AdbServerState,
    AdbServerStateSnapshot,
    AdbServerStateTransition,
)
from adb.tracking.snapshot.state import AdbDevicesSnapshotState


@dataclass(frozen=True, slots=True)
class AdbRuntimeState:
    """Authoritative server and device state owned by one ADB runtime."""

    server: AdbServerState
    devices: AdbDevicesSnapshotState

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerState):
            raise TypeError("server must be AdbServerState")
        if not isinstance(self.devices, AdbDevicesSnapshotState):
            raise TypeError("devices must be AdbDevicesSnapshotState")

    def observe_server(self) -> AdbServerStateSnapshot:
        """Capture the runtime-owned T0 server state for a lifecycle transaction."""

        return self.server.snapshot()

    def commit_server(self, transition: AdbServerStateTransition) -> bool:
        """Commit a T0 -> T1 server transition when T0 is still authoritative."""

        return self.server.commit(transition)


__all__ = ["AdbRuntimeState"]
