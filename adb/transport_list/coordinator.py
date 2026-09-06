from __future__ import annotations

from threading import RLock
from typing import Protocol, runtime_checkable

from adb.server.identity import AdbServerIdentity
from adb.server.state import AdbServerStateView
from adb.transport_list.model import AdbTransportList
from adb.transport_list.observation import AdbTransportListObservationBasis
from adb.transport_list.state import (
    AdbTransportListCoordinatedObservationResult,
    AdbTransportListObservationServerConflict,
    AdbTransportListObserved,
    AdbTransportListStateView,
    AdbTransportListStateWriter,
)
from eventing import EventPublisher


@runtime_checkable
class _AdbTransportListStateAccess(
    AdbTransportListStateView,
    AdbTransportListStateWriter,
    Protocol,
):
    """Read and commit authoritative transport-list state."""


@runtime_checkable
class _AdbTransportListAuthority(Protocol):
    """Runtime authority needed to fence server-bound transport-list observations."""

    @property
    def server(self) -> AdbServerStateView: ...

    @property
    def transport_list(self) -> _AdbTransportListStateAccess: ...

    def capture_transport_list_basis(
        self,
        server: AdbServerIdentity,
    ) -> AdbTransportListObservationBasis | None: ...

    def observe_transport_list(
        self,
        basis: AdbTransportListObservationBasis,
        transport_list: AdbTransportList,
    ) -> AdbTransportListCoordinatedObservationResult: ...


class _AdbTransportListStorePairAuthority:
    """Standalone authority adapter for callers that compose the two state stores directly."""

    def __init__(
        self,
        transport_list: _AdbTransportListStateAccess,
        server: AdbServerStateView,
    ) -> None:
        self._transport_list = transport_list
        self._server = server
        self._lock = RLock()

    @property
    def server(self) -> AdbServerStateView:
        return self._server

    @property
    def transport_list(self) -> _AdbTransportListStateAccess:
        return self._transport_list

    def capture_transport_list_basis(
        self,
        server: AdbServerIdentity,
    ) -> AdbTransportListObservationBasis | None:
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


class AdbTransportListCoordinator:
    """Orchestrate ordered transport-list observations through one authority boundary."""

    def __init__(
        self,
        authority_or_transport_list_state: _AdbTransportListAuthority
        | _AdbTransportListStateAccess,
        server_state: AdbServerStateView | None = None,
        *,
        publisher: EventPublisher | None = None,
    ) -> None:
        if isinstance(authority_or_transport_list_state, _AdbTransportListAuthority):
            if server_state is not None:
                raise ValueError(
                    "server_state must be omitted when a transport-list authority is provided"
                )
            authority = authority_or_transport_list_state
        else:
            if not isinstance(
                authority_or_transport_list_state,
                _AdbTransportListStateAccess,
            ):
                raise TypeError(
                    "authority_or_transport_list_state must satisfy the runtime transport-list "
                    "authority contract or AdbTransportListStateView and "
                    "AdbTransportListStateWriter"
                )
            if not isinstance(server_state, AdbServerStateView):
                raise TypeError(
                    "server_state must satisfy AdbServerStateView when composing state stores"
                )
            authority = _AdbTransportListStorePairAuthority(
                authority_or_transport_list_state,
                server_state,
            )
        if publisher is not None and not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher or be None")

        self._authority = authority
        self._publisher = publisher

    @property
    def server_state(self) -> AdbServerStateView:
        """Server authority used to fence transport-list observations."""

        return self._authority.server

    @property
    def transport_list_state(self) -> _AdbTransportListStateAccess:
        """Authoritative transport-list state committed by this coordinator."""

        return self._authority.transport_list

    def capture_basis(
        self,
        server: AdbServerIdentity,
    ) -> AdbTransportListObservationBasis | None:
        """Capture the authority basis immediately before a raw observation is read.

        ``None`` means ``server`` is no longer the authoritative active server lifetime and the
        caller must not start another read for that binding.
        """

        if not isinstance(server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        return self._authority.capture_transport_list_basis(server)

    def observe(
        self,
        basis: AdbTransportListObservationBasis,
        transport_list: AdbTransportList,
    ) -> AdbTransportListCoordinatedObservationResult:
        """Commit raw transport-list data when the captured authority fences still hold."""

        if not isinstance(basis, AdbTransportListObservationBasis):
            raise TypeError("basis must be AdbTransportListObservationBasis")
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")

        result = self._authority.observe_transport_list(basis, transport_list)
        if isinstance(result, AdbTransportListObserved) and self._publisher is not None:
            self._publisher.publish(result)
        return result


__all__ = [
    "AdbTransportListCoordinator",
    "AdbTransportListCoordinatedObservationResult",
    "AdbTransportListObservationServerConflict",
]
