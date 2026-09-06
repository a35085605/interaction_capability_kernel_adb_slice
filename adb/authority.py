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
    from adb.transport_list.model import AdbTransportList
    from adb.transport_list.observation import AdbTransportListObservationBasis
    from adb.transport_list.state import (
        AdbTransportListCoordinatedObservationResult,
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
    """Read-only aggregate views published by one runtime authority instance."""

    @property
    def server(self) -> AdbServerStateView: ...

    @property
    def transport_list(self) -> AdbTransportListStateView: ...


@runtime_checkable
class AdbServerAuthority(Protocol):
    """Narrow authority used by server lifecycle orchestration."""

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
class AdbTransportListObservationAuthority(Protocol):
    """Narrow authority for server-fenced transport-list observations."""

    def capture_transport_list_basis(
        self,
        server: AdbServerIdentity,
    ) -> AdbTransportListObservationBasis | None: ...

    def observe_transport_list(
        self,
        basis: AdbTransportListObservationBasis,
        transport_list: AdbTransportList,
    ) -> AdbTransportListCoordinatedObservationResult: ...


@runtime_checkable
class AdbTransportListInvalidationAuthority(Protocol):
    """Authority surface for fenced transport-list invalidation."""

    def invalidate_transport_list(
        self,
        expected: AdbTransportListIdentity,
    ) -> AdbTransportListInvalidationResult: ...


__all__ = [
    "AdbRuntimeAuthoritySnapshot",
    "AdbRuntimeAuthorityViews",
    "AdbServerAuthority",
    "AdbTransportListInvalidationAuthority",
    "AdbTransportListObservationAuthority",
]
