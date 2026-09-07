from __future__ import annotations

from threading import RLock

from adb.authority import AdbRuntimeAuthoritySnapshot
from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity
from adb.server.state import (
    AdbServerActivated,
    AdbServerActivationResult,
    AdbServerDeactivated,
    AdbServerDeactivationResult,
    AdbServerState,
    AdbServerStateStatus,
    AdbServerStateStore,
    AdbServerStateView,
)
from adb.transport_list.identity import AdbTransportListIdentity
from adb.transport_list.model import AdbTransportList
from adb.transport_list.session_identity import (
    AdbTransportListSessionIdentity,
    AdbTransportListSessionIdentityIssuer,
)
from adb.transport_list.state import (
    AdbTransportListInvalidationResult,
    AdbTransportListSessionAuthority,
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
    def session(self) -> AdbTransportListSessionIdentity | None:
        return self.snapshot().session

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
    """Own the runtime-wide linearization boundary for server retirement cascades.

    Transport-list session work does not call back into this runtime authority. Instead each active
    server lifetime owns one opaque, revocable ``AdbTransportListSessionIdentityIssuer``. Runtime
    wiring hands that capability to the watch. Server retirement revokes the issuer and the current
    transport-list session before releasing this authority lock.
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
            raise TypeError("transport_list must be AdbTransportListStateStore or None")

        initial_server = server.snapshot()
        initial_transport = transport_list.snapshot()
        if initial_transport.session is not None:
            raise ValueError(
                "runtime state cannot adopt an existing transport-list session without its issuer"
            )
        if transport_list.current_session_identity_issuer is not None:
            raise ValueError(
                "runtime state cannot adopt an existing transport-list session issuer"
            )

        self._server = server
        self._transport_list = transport_list
        if initial_server.active:
            transport_list.activate_session_identity_issuer()
        self._lock = RLock()
        self._server_view: AdbServerStateView = _AdbServerAuthorityView(self)
        self._transport_list_view: AdbTransportListStateView = _AdbTransportListAuthorityView(self)

    @property
    def server(self) -> AdbServerStateView:
        return self._server_view

    @property
    def transport_list(self) -> AdbTransportListStateView:
        return self._transport_list_view

    @property
    def transport_list_session_authority(self) -> AdbTransportListSessionAuthority:
        """Narrow child authority injected into transport-list coordination.

        Calls through this capability never inspect or snapshot runtime server state.
        """

        return self._transport_list

    @property
    def transport_list_session_issuer(
        self,
    ) -> AdbTransportListSessionIdentityIssuer | None:
        """Opaque admission capability for the currently active server lifetime."""

        with self._lock:
            return self._transport_list.current_session_identity_issuer

    def snapshot(self) -> AdbRuntimeAuthoritySnapshot:
        with self._lock:
            return AdbRuntimeAuthoritySnapshot(
                server=self._server.snapshot(),
                transport_list=self._transport_list.snapshot(),
            )

    def snapshot_server(self) -> AdbServerState:
        with self._lock:
            return self._server.snapshot()

    def activate_server(
        self,
        endpoint: AdbServerEndpoint,
        *,
        expected: AdbServerIdentity | None,
    ) -> AdbServerActivationResult:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if expected is not None and not isinstance(expected, AdbServerIdentity):
            raise TypeError("expected must be AdbServerIdentity or None")

        with self._lock:
            activation = self._server.activate(endpoint, expected=expected)
            if isinstance(activation, AdbServerActivated):
                self._transport_list.activate_session_identity_issuer()
            return activation

    def deactivate_server(
        self,
        expected: AdbServerIdentity,
    ) -> AdbServerDeactivationResult:
        """Atomically retire server admission and invalidate transport-list session authority."""

        if not isinstance(expected, AdbServerIdentity):
            raise TypeError("expected must be AdbServerIdentity")

        with self._lock:
            deactivation = self._server.deactivate(expected)
            if not isinstance(deactivation, AdbServerDeactivated):
                return deactivation

            issuer = self._transport_list.current_session_identity_issuer
            if issuer is not None:
                self._transport_list.revoke_session_identity_issuer(issuer)
            else:
                self._transport_list.revoke_current_session()
            transport_state = self._transport_list.snapshot()
            if (
                transport_state.status is not AdbTransportListStateStatus.INVALIDATED
                or transport_state.session is not None
            ):
                raise RuntimeError(
                    "server retirement must leave transport-list authority invalidated and revoked"
                )
            return deactivation

    def activate(
        self,
        endpoint: AdbServerEndpoint,
        *,
        expected: AdbServerIdentity | None,
    ) -> AdbServerActivationResult:
        return self.activate_server(endpoint, expected=expected)

    def deactivate(self, expected: AdbServerIdentity) -> AdbServerDeactivationResult:
        return self.deactivate_server(expected)

    def snapshot_transport_list(self) -> AdbTransportListState:
        with self._lock:
            return self._transport_list.snapshot()

    def invalidate_transport_list(
        self,
        expected: AdbTransportListIdentity,
    ) -> AdbTransportListInvalidationResult:
        """Compatibility invalidation entry point serialized with server retirement."""

        if not isinstance(expected, AdbTransportListIdentity):
            raise TypeError("expected must be AdbTransportListIdentity")
        with self._lock:
            return self._transport_list.invalidate(expected)


AdbRuntimeState = AdbRuntimeAuthorityStateStore


__all__ = [
    "AdbRuntimeAuthoritySnapshot",
    "AdbRuntimeAuthorityStateStore",
    "AdbRuntimeState",
]
