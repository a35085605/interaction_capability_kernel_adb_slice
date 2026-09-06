from __future__ import annotations

from threading import RLock

from adb.authority import AdbRuntimeAuthoritySnapshot
from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity
from adb.server.state import (
    AdbServerActivationResult,
    AdbServerDeactivationResult,
    AdbServerState,
    AdbServerStateStatus,
    AdbServerStateStore,
    AdbServerStateView,
)
from adb.transport_list.identity import AdbTransportListIdentity
from adb.transport_list.model import AdbTransportList
from adb.transport_list.observation import AdbTransportListObservationBasis
from adb.transport_list.state import (
    AdbTransportListCoordinatedObservationResult,
    AdbTransportListInvalidationResult,
    AdbTransportListObservationServerConflict,
    AdbTransportListState,
    AdbTransportListStateStatus,
    AdbTransportListStateStore,
    AdbTransportListStateView,
)


class _AdbServerAuthorityView:
    """Read-only server-state facade backed by the runtime authority lock."""

    __slots__ = ("_authority",)

    def __init__(self, authority: AdbRuntimeAuthorityStateStore) -> None:
        self._authority = authority

    @property
    def endpoint(self) -> AdbServerEndpoint | None:
        return self.snapshot().endpoint

    @property
    def identity(self) -> AdbServerIdentity | None:
        return self.snapshot().identity

    @property
    def status(self) -> AdbServerStateStatus:
        return self.snapshot().status

    @property
    def active(self) -> bool:
        return self.snapshot().active

    @property
    def current_identity(self) -> AdbServerIdentity | None:
        return self.snapshot().current_identity

    def snapshot(self) -> AdbServerState:
        return self._authority.snapshot_server()


class _AdbTransportListAuthorityView:
    """Read-only transport-list facade backed by the runtime authority lock."""

    __slots__ = ("_authority",)

    def __init__(self, authority: AdbRuntimeAuthorityStateStore) -> None:
        self._authority = authority

    @property
    def identity(self) -> AdbTransportListIdentity | None:
        return self.snapshot().identity

    @property
    def status(self) -> AdbTransportListStateStatus:
        return self.snapshot().status

    @property
    def current(self) -> AdbTransportList | None:
        return self.snapshot().current

    @property
    def current_identity(self) -> AdbTransportListIdentity | None:
        return self.snapshot().current_identity

    def snapshot(self) -> AdbTransportListState:
        return self._authority.snapshot_transport_list()


class AdbRuntimeAuthorityStateStore:
    """Own the runtime-wide linearization boundary for authoritative ADB state.

    Server state is the authority root and transport-list state is a server-lifetime-bound
    projection. Their underlying stores remain private implementation details; all authoritative
    writes and all cross-aggregate reads enter through this store's lock.
    """

    def __init__(
        self,
        server: AdbServerStateStore | None = None,
        transport_list: AdbTransportListStateStore | None = None,
    ) -> None:
        if server is None:
            server = AdbServerStateStore()
        elif not isinstance(server, AdbServerStateStore):
            raise TypeError("server must be AdbServerStateStore or None")
        if transport_list is None:
            transport_list = AdbTransportListStateStore()
        elif not isinstance(transport_list, AdbTransportListStateStore):
            raise TypeError(
                "transport_list must be AdbTransportListStateStore or None"
            )

        self._server = server
        self._transport_list = transport_list
        self._lock = RLock()
        self._server_view: AdbServerStateView = _AdbServerAuthorityView(self)
        self._transport_list_view: AdbTransportListStateView = (
            _AdbTransportListAuthorityView(self)
        )

    @property
    def server(self) -> AdbServerStateView:
        """Read-only authoritative server-state view."""

        return self._server_view

    @property
    def transport_list(self) -> AdbTransportListStateView:
        """Read-only authoritative transport-list state view."""

        return self._transport_list_view

    # ------------------------------------------------------------------
    # Combined authority snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> AdbRuntimeAuthoritySnapshot:
        """Atomically capture server and transport-list state at one authority boundary."""

        with self._lock:
            return AdbRuntimeAuthoritySnapshot(
                server=self._server.snapshot(),
                transport_list=self._transport_list.snapshot(),
            )

    # ------------------------------------------------------------------
    # Server authority surface
    # ------------------------------------------------------------------

    def snapshot_server(self) -> AdbServerState:
        """Atomically capture server state at the runtime authority boundary."""

        with self._lock:
            return self._server.snapshot()

    def activate_server(
        self,
        endpoint: AdbServerEndpoint,
        *,
        expected: AdbServerIdentity | None,
    ) -> AdbServerActivationResult:
        """Commit server activation while excluding cross-aggregate commits."""

        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if expected is not None and not isinstance(expected, AdbServerIdentity):
            raise TypeError("expected must be AdbServerIdentity or None")
        with self._lock:
            return self._server.activate(endpoint, expected=expected)

    def deactivate_server(
        self,
        expected: AdbServerIdentity,
    ) -> AdbServerDeactivationResult:
        """Commit server deactivation at the runtime authority boundary."""

        if not isinstance(expected, AdbServerIdentity):
            raise TypeError("expected must be AdbServerIdentity")
        with self._lock:
            return self._server.deactivate(expected)

    # Compatibility aliases retain the old direct authority API without exposing child writers.
    def activate(
        self,
        endpoint: AdbServerEndpoint,
        *,
        expected: AdbServerIdentity | None,
    ) -> AdbServerActivationResult:
        return self.activate_server(endpoint, expected=expected)

    def deactivate(self, expected: AdbServerIdentity) -> AdbServerDeactivationResult:
        return self.deactivate_server(expected)

    # ------------------------------------------------------------------
    # Server-bound transport-list authority surface
    # ------------------------------------------------------------------

    def snapshot_transport_list(self) -> AdbTransportListState:
        """Atomically capture transport-list state at the runtime authority boundary."""

        with self._lock:
            return self._transport_list.snapshot()

    def capture_transport_list_basis(
        self,
        server: AdbServerIdentity,
    ) -> AdbTransportListObservationBasis | None:
        """Capture a transport-list read basis iff ``server`` is still authoritative."""

        if not isinstance(server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")

        with self._lock:
            if self._server.current_identity != server:
                return None
            state = self._transport_list.snapshot()
            return AdbTransportListObservationBasis(
                server=server,
                transport_list_identity=state.identity,
            )

    def observe_transport_list(
        self,
        basis: AdbTransportListObservationBasis,
        transport_list: AdbTransportList,
    ) -> AdbTransportListCoordinatedObservationResult:
        """Commit a list only when both server and transport-list authority fences hold."""

        if not isinstance(basis, AdbTransportListObservationBasis):
            raise TypeError("basis must be AdbTransportListObservationBasis")
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")

        with self._lock:
            current_server = self._server.current_identity
            if current_server != basis.server:
                return AdbTransportListObservationServerConflict(
                    basis=basis,
                    transport_list=transport_list,
                    current_server=current_server,
                    state=self._transport_list.snapshot(),
                )
            expected = self._transport_list.snapshot()
            return self._transport_list.observe(
                basis,
                transport_list,
                expected,
            )

    def invalidate_transport_list(
        self,
        expected: AdbTransportListIdentity,
    ) -> AdbTransportListInvalidationResult:
        """Invalidate one transport-list identity through the runtime authority boundary."""

        if not isinstance(expected, AdbTransportListIdentity):
            raise TypeError("expected must be AdbTransportListIdentity")
        with self._lock:
            return self._transport_list.invalidate(expected)


# Compatibility name retained for callers that previously treated runtime state as a
# two-store value object. The runtime state is now the authority store itself.
AdbRuntimeState = AdbRuntimeAuthorityStateStore


__all__ = [
    "AdbRuntimeAuthoritySnapshot",
    "AdbRuntimeAuthorityStateStore",
    "AdbRuntimeState",
]
