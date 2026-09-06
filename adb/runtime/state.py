from __future__ import annotations

from threading import RLock

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity
from adb.server.state import (
    AdbServerActivationResult,
    AdbServerDeactivationResult,
    AdbServerState,
    AdbServerStateStatus,
    AdbServerStateStore,
)
from adb.transport_list.model import AdbTransportList
from adb.transport_list.observation import AdbTransportListObservationBasis
from adb.transport_list.state import (
    AdbTransportListCoordinatedObservationResult,
    AdbTransportListObservationServerConflict,
    AdbTransportListStateStore,
)


class AdbRuntimeAuthorityStateStore:
    """Own the runtime-wide linearization boundary for authoritative ADB state.

    Server state is the authority root and transport-list state is a server-lifetime-bound
    projection.  Their individual stores remain independently thread-safe, while this store
    serializes transitions whose correctness spans both aggregates.
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

    @property
    def server(self) -> AdbServerStateStore:
        """Runtime-owned authoritative server store exposed to server-only readers."""

        return self._server

    @property
    def transport_list(self) -> AdbTransportListStateStore:
        """Runtime-owned authoritative transport-list store exposed to list-only readers."""

        return self._transport_list

    # ------------------------------------------------------------------
    # Server authority surface
    # ------------------------------------------------------------------

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
        """Atomically capture server state at the runtime authority boundary."""

        with self._lock:
            return self._server.snapshot()

    def activate(
        self,
        endpoint: AdbServerEndpoint,
        *,
        expected: AdbServerIdentity | None,
    ) -> AdbServerActivationResult:
        """Commit a server activation while excluding cross-aggregate observation commits."""

        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if expected is not None and not isinstance(expected, AdbServerIdentity):
            raise TypeError("expected must be AdbServerIdentity or None")
        with self._lock:
            return self._server.activate(endpoint, expected=expected)

    def deactivate(self, expected: AdbServerIdentity) -> AdbServerDeactivationResult:
        """Commit a server deactivation at the runtime authority boundary."""

        if not isinstance(expected, AdbServerIdentity):
            raise TypeError("expected must be AdbServerIdentity")
        with self._lock:
            return self._server.deactivate(expected)

    # ------------------------------------------------------------------
    # Server-bound transport-list authority surface
    # ------------------------------------------------------------------

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


# Compatibility name retained for callers that previously treated runtime state as a
# two-store value object.  The runtime state is now the authority store itself.
AdbRuntimeState = AdbRuntimeAuthorityStateStore


__all__ = ["AdbRuntimeAuthorityStateStore", "AdbRuntimeState"]
