from __future__ import annotations

from dataclasses import dataclass

from adb.server.endpoint import AdbServerEndpoint
from adb.server.epoch import ServerEpoch
from adb.server.lifetime import AdbServerLifetime
from adb.server.state import AdbServerState, AdbServerStateSnapshot
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
        """Capture the runtime-owned atomic server state for a lifecycle transaction."""

        return self.server.snapshot()

    def commit_server(
        self,
        endpoint: AdbServerEndpoint,
        expected_epoch: ServerEpoch | None,
    ) -> AdbServerLifetime | None:
        """Activate an endpoint when the observed inactive epoch is still authoritative."""

        return self.server.commit(endpoint, expected_epoch)

    def deactivate_server(self, expected: AdbServerLifetime) -> bool:
        """Deactivate the expected authoritative server lifetime without advancing its epoch."""

        return self.server.deactivate(expected)


__all__ = ["AdbRuntimeState"]
