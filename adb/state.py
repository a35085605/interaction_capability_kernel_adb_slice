from __future__ import annotations

from dataclasses import dataclass

from adb.server.state import AdbServerState
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


__all__ = ["AdbRuntimeState"]
