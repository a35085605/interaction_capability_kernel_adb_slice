from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from adb.server.endpoint import AdbServerEndpoint
    from adb.server.identity import AdbServerIdentity
    from adb.server.state import (
        AdbServerActivationResult,
        AdbServerDeactivationResult,
        AdbServerState,
        AdbServerStateView,
    )
    from adb.transport_list.identity import AdbTransportListIdentity
    from adb.transport_list.state import (
        AdbTransportListInvalidationResult,
        AdbTransportListState,
        AdbTransportListStateView,
    )


@dataclass(frozen=True, slots=True)
class AdbRuntimeAuthoritySnapshot:
    """Point-in-time snapshot of the runtime's coupled authoritative state."""

    server: AdbServerState
    transport_list: AdbTransportListState


@runtime_checkable
class AdbRuntimeAuthorityViews(Protocol):
    @property
    def server(self) -> AdbServerStateView: ...

    @property
    def transport_list(self) -> AdbTransportListStateView: ...


@runtime_checkable
class AdbServerAuthority(Protocol):
    def snapshot_server(self) -> AdbServerState: ...

    def activate_server(
        self,
        endpoint: AdbServerEndpoint,
        *,
        expected: AdbServerIdentity | None,
    ) -> AdbServerActivationResult: ...

    def deactivate_server(
        self,
        expected: AdbServerIdentity,
    ) -> AdbServerDeactivationResult: ...


@runtime_checkable
class AdbTransportListInvalidationAuthority(Protocol):
    def invalidate_transport_list(
        self,
        expected: AdbTransportListIdentity,
    ) -> AdbTransportListInvalidationResult: ...


__all__ = [
    "AdbRuntimeAuthoritySnapshot",
    "AdbRuntimeAuthorityViews",
    "AdbServerAuthority",
    "AdbTransportListInvalidationAuthority",
]
