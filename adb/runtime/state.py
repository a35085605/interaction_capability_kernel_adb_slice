from __future__ import annotations

from dataclasses import dataclass

from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifetime import AdbServerLifetime
from adb.server.state import AdbServerState, AdbServerStateStore
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

    def observe_server(self) -> AdbServerState:
        """Capture the runtime-owned atomic server state for a lifecycle transaction."""

        return self.server.snapshot()

    def commit_server(
        self,
        endpoint: AdbServerEndpoint,
        expected: AdbServerState,
    ) -> AdbServerLifetime | None:
        """Activate an endpoint when the observed inactive state is still authoritative."""

        return self.server.commit(endpoint, expected)

    def deactivate_server(self, expected: AdbServerLifetime) -> bool:
        """Deactivate the expected authoritative server lifetime without replacing its identity."""

        return self.server.deactivate(expected)


__all__ = ["AdbRuntimeState"]
