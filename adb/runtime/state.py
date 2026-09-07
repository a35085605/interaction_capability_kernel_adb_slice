from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

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
    AdbServerStateWriter,
)
from adb.transport_list.identity import AdbTransportListIdentity
from adb.transport_list.model import AdbTransportList
from adb.transport_list.observation import AdbTransportListObservation
from adb.transport_list.session_identity import (
    AdbTransportListSessionIdentity,
    AdbTransportListSessionIdentityIssuer,
)
from adb.transport_list.state import (
    AdbTransportListInvalidated,
    AdbTransportListInvalidationResult,
    AdbTransportListObservationResult,
    AdbTransportListObservationStateConflict,
    AdbTransportListObserved,
    AdbTransportListSessionAuthority,
    AdbTransportListSessionBegun,
    AdbTransportListSessionRevocationResult,
    AdbTransportListSessionRevocationStateConflict,
    AdbTransportListSessionRevoked,
    AdbTransportListState,
    AdbTransportListStateStatus,
    AdbTransportListStateStore,
    AdbTransportListStateView,
)
from adb.transport_list.watch_session_state import (
    AdbTransportListWatchSessionActivated,
    AdbTransportListWatchSessionDeactivated,
    AdbTransportListWatchSessionState,
    AdbTransportListWatchSessionStateStatus,
    AdbTransportListWatchSessionStateStore,
    AdbTransportListWatchSessionStateView,
)


@dataclass(frozen=True, slots=True)
class AdbRuntimeAuthoritySnapshot:
    """Point-in-time snapshot of the state coordinated by this runtime."""

    server: AdbServerState
    transport_list_watch_session: AdbTransportListWatchSessionState
    transport_list: AdbTransportListState


class _AdbRuntimeServerStateView:
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


class _AdbRuntimeServerStateWriter:
    """Adapt server-local writes to the runtime's synchronized transitions."""

    __slots__ = ("_state",)

    def __init__(self, state: AdbRuntimeAuthorityStateStore) -> None:
        self._state = state

    def activate(
        self,
        endpoint: AdbServerEndpoint,
        identity: AdbServerIdentity,
        *,
        expected: AdbServerIdentity | None,
    ) -> AdbServerActivationResult:
        return self._state.activate_server(endpoint, identity, expected=expected)

    def deactivate(self, expected: AdbServerIdentity) -> AdbServerDeactivationResult:
        return self._state.deactivate_server(expected)


class _AdbRuntimeTransportListWatchSessionStateView:
    """Read-only watch-session facade backed by the runtime authority lock."""

    __slots__ = ("_authority",)

    def __init__(self, authority: AdbRuntimeAuthorityStateStore) -> None:
        self._authority = authority

    @property
    def identity(self) -> AdbTransportListSessionIdentity | None:
        return self.snapshot().identity

    @property
    def status(self) -> AdbTransportListWatchSessionStateStatus:
        return self.snapshot().status

    @property
    def active(self) -> bool:
        return self.snapshot().active

    @property
    def current_identity(self) -> AdbTransportListSessionIdentity | None:
        return self.snapshot().current_identity

    def snapshot(self) -> AdbTransportListWatchSessionState:
        return self._authority.snapshot_transport_list_watch_session()


class _AdbRuntimeTransportListStateView:
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


class _AdbRuntimeTransportListSessionAuthority:
    """Narrow session/observation authority backed by runtime-coordinated state."""

    __slots__ = ("_authority",)

    def __init__(self, authority: AdbRuntimeAuthorityStateStore) -> None:
        self._authority = authority

    def snapshot(self) -> AdbTransportListWatchSessionState:
        return self._authority.snapshot_transport_list_watch_session()

    def begin_session(self) -> AdbTransportListSessionBegun | None:
        return self._authority.begin_transport_list_session()

    def can_observe_update(
        self,
        session: AdbTransportListSessionIdentity,
    ) -> bool:
        return self._authority.can_observe_transport_list_update(session)

    def revoke_session(
        self,
        session: AdbTransportListSessionIdentity,
    ) -> AdbTransportListSessionRevocationResult:
        return self._authority.revoke_transport_list_session(session)

    def observe_initial(
        self,
        session: AdbTransportListSessionIdentity,
        transport_list: AdbTransportList,
    ) -> AdbTransportListObservationResult:
        return self._authority.observe_initial_transport_list(session, transport_list)

    def observe_update(
        self,
        session: AdbTransportListSessionIdentity,
        transport_list: AdbTransportList,
    ) -> AdbTransportListObservationResult:
        return self._authority.observe_transport_list_update(session, transport_list)


class AdbRuntimeAuthorityStateStore:
    """Coordinate server, watch-session, and transport-list state within the runtime.

    Only this runtime layer maps server lifetimes to transport-list watch-session authority and
    coordinates that producer authority with transport-list projection visibility. Child stores
    remain independent state machines; cross-store fences and transition ordering live here.

    One runtime-scoped watch-session identity issuer spans all server lifetimes. Read-only facades
    share the runtime lock so consumers cannot observe partially coordinated transitions. Supplied
    stores transfer mutation ownership to the runtime and must not be written directly.
    """

    def __init__(
        self,
        server: AdbServerStateStore | None = None,
        transport_list: AdbTransportListStateStore | None = None,
        transport_list_watch_session: AdbTransportListWatchSessionStateStore | None = None,
        *,
        session_identity_issuer: AdbTransportListSessionIdentityIssuer | None = None,
    ) -> None:
        if server is None:
            server = AdbServerStateStore()
        elif not isinstance(server, AdbServerStateStore):
            raise TypeError("server must be AdbServerStateStore or None")
        if transport_list is None:
            transport_list = AdbTransportListStateStore()
        elif not isinstance(transport_list, AdbTransportListStateStore):
            raise TypeError("transport_list must be AdbTransportListStateStore or None")
        if transport_list_watch_session is None:
            transport_list_watch_session = AdbTransportListWatchSessionStateStore()
        elif not isinstance(
            transport_list_watch_session,
            AdbTransportListWatchSessionStateStore,
        ):
            raise TypeError(
                "transport_list_watch_session must be "
                "AdbTransportListWatchSessionStateStore or None"
            )
        if session_identity_issuer is None:
            session_identity_issuer = AdbTransportListSessionIdentityIssuer()
        elif not isinstance(
            session_identity_issuer,
            AdbTransportListSessionIdentityIssuer,
        ):
            raise TypeError(
                "session_identity_issuer must be AdbTransportListSessionIdentityIssuer or None"
            )

        initial_session = transport_list_watch_session.snapshot()
        initial_transport = transport_list.snapshot()
        if initial_session.active:
            raise ValueError("runtime state cannot adopt an active transport-list watch session")
        if initial_transport.status is not AdbTransportListStateStatus.INVALIDATED:
            raise ValueError("runtime state cannot adopt a current transport-list projection")

        self._server = server
        self._transport_list = transport_list
        self._transport_list_watch_session = transport_list_watch_session
        self._session_identity_issuer = session_identity_issuer
        self._lock = RLock()
        self._server_view: AdbServerStateView = _AdbRuntimeServerStateView(self)
        self._server_writer: AdbServerStateWriter = _AdbRuntimeServerStateWriter(self)
        self._transport_list_watch_session_view: AdbTransportListWatchSessionStateView = (
            _AdbRuntimeTransportListWatchSessionStateView(self)
        )
        self._transport_list_view: AdbTransportListStateView = _AdbRuntimeTransportListStateView(
            self
        )
        self._transport_list_session_authority: AdbTransportListSessionAuthority = (
            _AdbRuntimeTransportListSessionAuthority(self)
        )

    @property
    def server(self) -> AdbServerStateView:
        return self._server_view

    @property
    def server_writer(self) -> AdbServerStateWriter:
        """Server-local write interface whose implementation coordinates runtime state."""

        return self._server_writer

    @property
    def transport_list_watch_session(self) -> AdbTransportListWatchSessionStateView:
        return self._transport_list_watch_session_view

    @property
    def transport_list(self) -> AdbTransportListStateView:
        return self._transport_list_view

    @property
    def transport_list_session_authority(self) -> AdbTransportListSessionAuthority:
        """Narrow child authority whose transitions are coordinated by the runtime."""

        return self._transport_list_session_authority

    def snapshot(self) -> AdbRuntimeAuthoritySnapshot:
        with self._lock:
            return AdbRuntimeAuthoritySnapshot(
                server=self._server.snapshot(),
                transport_list_watch_session=self._transport_list_watch_session.snapshot(),
                transport_list=self._transport_list.snapshot(),
            )

    def snapshot_server(self) -> AdbServerState:
        with self._lock:
            return self._server.snapshot()

    def snapshot_transport_list_watch_session(self) -> AdbTransportListWatchSessionState:
        with self._lock:
            return self._transport_list_watch_session.snapshot()

    def snapshot_transport_list(self) -> AdbTransportListState:
        with self._lock:
            return self._transport_list.snapshot()

    def activate_server(
        self,
        endpoint: AdbServerEndpoint,
        identity: AdbServerIdentity,
        *,
        expected: AdbServerIdentity | None,
    ) -> AdbServerActivationResult:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if not isinstance(identity, AdbServerIdentity):
            raise TypeError("identity must be AdbServerIdentity")
        if expected is not None and not isinstance(expected, AdbServerIdentity):
            raise TypeError("expected must be AdbServerIdentity or None")

        with self._lock:
            return self._server.activate(endpoint, identity, expected=expected)

    def deactivate_server(
        self,
        expected: AdbServerIdentity,
    ) -> AdbServerDeactivationResult:
        """Atomically retire the server and revoke its transport-list producer authority."""

        if not isinstance(expected, AdbServerIdentity):
            raise TypeError("expected must be AdbServerIdentity")

        with self._lock:
            deactivation = self._server.deactivate(expected)
            if not isinstance(deactivation, AdbServerDeactivated):
                return deactivation

            self._revoke_current_transport_list_session_locked()
            session_state = self._transport_list_watch_session.snapshot()
            transport_state = self._transport_list.snapshot()
            if session_state.active or (
                transport_state.status is not AdbTransportListStateStatus.INVALIDATED
            ):
                raise RuntimeError(
                    "server retirement must leave watch-session authority inactive and "
                    "transport-list projection invalidated"
                )
            return deactivation

    def activate(
        self,
        endpoint: AdbServerEndpoint,
        identity: AdbServerIdentity,
        *,
        expected: AdbServerIdentity | None,
    ) -> AdbServerActivationResult:
        return self.activate_server(endpoint, identity, expected=expected)

    def deactivate(self, expected: AdbServerIdentity) -> AdbServerDeactivationResult:
        return self.deactivate_server(expected)

    def begin_transport_list_session(self) -> AdbTransportListSessionBegun | None:
        """Install a fresh watch-session authority while the server is active."""

        with self._lock:
            if not self._server.snapshot().active:
                return None

            session_state = self._transport_list_watch_session.snapshot()
            transport_state = self._transport_list.snapshot()
            superseded_session = session_state.current_identity
            invalidated_identity = transport_state.current_identity

            if superseded_session is not None:
                deactivation = self._transport_list_watch_session.deactivate(
                    superseded_session
                )
                if not isinstance(
                    deactivation,
                    AdbTransportListWatchSessionDeactivated,
                ):
                    raise RuntimeError(
                        "runtime lost the current watch-session deactivation fence"
                    )
            elif transport_state.current_identity is not None:
                raise RuntimeError(
                    "current transport-list projection requires an active watch session"
                )

            self._invalidate_current_transport_list_locked()

            session = self._session_identity_issuer.issue()
            expected_identity = self._transport_list_watch_session.snapshot().identity
            activation = self._transport_list_watch_session.activate(
                session,
                expected=expected_identity,
            )
            if not isinstance(activation, AdbTransportListWatchSessionActivated):
                raise RuntimeError("runtime lost the watch-session activation fence")

            return AdbTransportListSessionBegun(
                session=session,
                superseded_session=superseded_session,
                invalidated_identity=invalidated_identity,
            )

    def can_observe_transport_list_update(
        self,
        session: AdbTransportListSessionIdentity,
    ) -> bool:
        if not isinstance(session, AdbTransportListSessionIdentity):
            raise TypeError("session must be AdbTransportListSessionIdentity")

        with self._lock:
            session_state = self._transport_list_watch_session.snapshot()
            transport_state = self._transport_list.snapshot()
            return (
                self._server.snapshot().active
                and session_state.current_identity is session
                and transport_state.status is AdbTransportListStateStatus.CURRENT
            )

    def revoke_transport_list_session(
        self,
        session: AdbTransportListSessionIdentity,
    ) -> AdbTransportListSessionRevocationResult:
        if not isinstance(session, AdbTransportListSessionIdentity):
            raise TypeError("session must be AdbTransportListSessionIdentity")

        with self._lock:
            session_state = self._transport_list_watch_session.snapshot()
            if session_state.current_identity is not session:
                return AdbTransportListSessionRevocationStateConflict(
                    session,
                    session_state,
                )

            invalidated_identity = self._transport_list.snapshot().current_identity
            deactivation = self._transport_list_watch_session.deactivate(session)
            if not isinstance(deactivation, AdbTransportListWatchSessionDeactivated):
                raise RuntimeError("runtime lost the watch-session deactivation fence")
            self._invalidate_current_transport_list_locked()
            return AdbTransportListSessionRevoked(session, invalidated_identity)

    def observe_initial_transport_list(
        self,
        session: AdbTransportListSessionIdentity,
        transport_list: AdbTransportList,
    ) -> AdbTransportListObservationResult:
        return self._observe_transport_list(
            session,
            transport_list,
            required_status=AdbTransportListStateStatus.INVALIDATED,
        )

    def observe_transport_list_update(
        self,
        session: AdbTransportListSessionIdentity,
        transport_list: AdbTransportList,
    ) -> AdbTransportListObservationResult:
        return self._observe_transport_list(
            session,
            transport_list,
            required_status=AdbTransportListStateStatus.CURRENT,
        )

    def _observe_transport_list(
        self,
        session: AdbTransportListSessionIdentity,
        transport_list: AdbTransportList,
        *,
        required_status: AdbTransportListStateStatus,
    ) -> AdbTransportListObservationResult:
        if not isinstance(session, AdbTransportListSessionIdentity):
            raise TypeError("session must be AdbTransportListSessionIdentity")
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")
        if not isinstance(required_status, AdbTransportListStateStatus):
            raise TypeError("required_status must be AdbTransportListStateStatus")

        with self._lock:
            session_state = self._transport_list_watch_session.snapshot()
            current = self._transport_list.snapshot()
            if (
                not self._server.snapshot().active
                or session_state.current_identity is not session
                or current.status is not required_status
            ):
                return AdbTransportListObservationStateConflict(
                    session=session,
                    transport_list=transport_list,
                    state=current,
                )

            next_state = (
                self._transport_list.observe_initial(transport_list)
                if required_status is AdbTransportListStateStatus.INVALIDATED
                else self._transport_list.observe_update(transport_list)
            )
            if next_state is None or next_state.identity is None:
                raise RuntimeError(
                    "transport-list projection changed after the runtime authority fence"
                )

            return AdbTransportListObserved(
                AdbTransportListObservation(
                    session=session,
                    identity=next_state.identity,
                    transport_list=transport_list,
                )
            )

    def invalidate_transport_list(
        self,
        expected: AdbTransportListIdentity,
    ) -> AdbTransportListInvalidationResult:
        """Compatibility invalidation serialized with watch-session revocation."""

        if not isinstance(expected, AdbTransportListIdentity):
            raise TypeError("expected must be AdbTransportListIdentity")
        with self._lock:
            result = self._transport_list.invalidate(expected)
            if isinstance(result, AdbTransportListInvalidated):
                session = self._transport_list_watch_session.snapshot().current_identity
                if session is not None:
                    deactivation = self._transport_list_watch_session.deactivate(session)
                    if not isinstance(
                        deactivation,
                        AdbTransportListWatchSessionDeactivated,
                    ):
                        raise RuntimeError(
                            "runtime lost the watch-session deactivation fence after invalidation"
                        )
            return result

    def _invalidate_current_transport_list_locked(
        self,
    ) -> AdbTransportListIdentity | None:
        current = self._transport_list.snapshot()
        identity = current.current_identity
        if identity is None:
            if current.status is not AdbTransportListStateStatus.INVALIDATED:
                raise RuntimeError(
                    "transport-list state without a current identity must be invalidated"
                )
            return None

        result = self._transport_list.invalidate(identity)
        if not isinstance(result, AdbTransportListInvalidated):
            raise RuntimeError("runtime lost the current transport-list invalidation fence")
        return identity

    def _revoke_current_transport_list_session_locked(
        self,
    ) -> AdbTransportListSessionRevoked | None:
        session_state = self._transport_list_watch_session.snapshot()
        session = session_state.current_identity
        transport_state = self._transport_list.snapshot()
        if session is None:
            if transport_state.current_identity is not None:
                raise RuntimeError(
                    "current transport-list projection requires an active watch session"
                )
            return None

        invalidated_identity = transport_state.current_identity
        deactivation = self._transport_list_watch_session.deactivate(session)
        if not isinstance(deactivation, AdbTransportListWatchSessionDeactivated):
            raise RuntimeError("runtime lost the current watch-session deactivation fence")
        self._invalidate_current_transport_list_locked()
        return AdbTransportListSessionRevoked(session, invalidated_identity)


AdbRuntimeState = AdbRuntimeAuthorityStateStore


__all__ = [
    "AdbRuntimeAuthoritySnapshot",
    "AdbRuntimeAuthorityStateStore",
    "AdbRuntimeState",
]
