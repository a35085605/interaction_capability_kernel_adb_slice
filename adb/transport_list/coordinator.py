from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Protocol, TypeAlias, runtime_checkable

from adb.server.identity import AdbServerIdentity
from adb.server.state import AdbServerStateView
from adb.transport_list.model import AdbTransportList
from adb.transport_list.observation import AdbTransportListObservationBasis
from adb.transport_list.state import (
    AdbTransportListObservationResult,
    AdbTransportListObserved,
    AdbTransportListState,
    AdbTransportListStateView,
    AdbTransportListStateWriter,
)
from eventing import EventPublisher


_RLockType = type(RLock())


@runtime_checkable
class _AdbTransportListStateAccess(
    AdbTransportListStateView,
    AdbTransportListStateWriter,
    Protocol,
):
    """Read and commit authoritative transport-list state."""


@dataclass(frozen=True, slots=True)
class AdbTransportListObservationServerConflict:
    """Evidence that raw transport-list data belongs to a non-authoritative server lifetime."""

    basis: AdbTransportListObservationBasis
    transport_list: AdbTransportList
    current_server: AdbServerIdentity | None
    state: AdbTransportListState

    def __post_init__(self) -> None:
        if not isinstance(self.basis, AdbTransportListObservationBasis):
            raise TypeError("basis must be AdbTransportListObservationBasis")
        if not isinstance(self.transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")
        if self.current_server is not None and not isinstance(
            self.current_server, AdbServerIdentity
        ):
            raise TypeError("current_server must be AdbServerIdentity or None")
        if not isinstance(self.state, AdbTransportListState):
            raise TypeError("state must be AdbTransportListState")
        if self.current_server == self.basis.server:
            raise ValueError("server conflict requires a different authoritative server")

    @property
    def server(self) -> AdbServerIdentity:
        return self.basis.server

    def __bool__(self) -> bool:
        return False


AdbTransportListCoordinatedObservationResult: TypeAlias = (
    AdbTransportListObservationResult | AdbTransportListObservationServerConflict
)


class AdbTransportListCoordinator:
    """Capture observation authority and commit the ordered transport-list watch stream."""

    def __init__(
        self,
        transport_list_state: _AdbTransportListStateAccess,
        server_state: AdbServerStateView,
        *,
        publisher: EventPublisher | None = None,
        authority_lock: _RLockType | None = None,
    ) -> None:
        if not isinstance(transport_list_state, _AdbTransportListStateAccess):
            raise TypeError(
                "transport_list_state must satisfy AdbTransportListStateView and "
                "AdbTransportListStateWriter"
            )
        if not isinstance(server_state, AdbServerStateView):
            raise TypeError("server_state must satisfy AdbServerStateView")
        if publisher is not None and not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher or be None")
        if authority_lock is not None and not isinstance(authority_lock, _RLockType):
            raise TypeError("authority_lock must be a reentrant lock or None")
        self._transport_list_state = transport_list_state
        self._server_state = server_state
        self._publisher = publisher
        self._lock = RLock() if authority_lock is None else authority_lock

    @property
    def server_state(self) -> AdbServerStateView:
        """Server authority used to fence transport-list observations."""

        return self._server_state

    @property
    def transport_list_state(self) -> _AdbTransportListStateAccess:
        """Authoritative transport-list state committed by this coordinator."""

        return self._transport_list_state

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

        with self._lock:
            if self._server_state.current_identity != server:
                return None
            transport_list_state = self._transport_list_state.snapshot()
            return AdbTransportListObservationBasis(
                server=server,
                transport_list_identity=transport_list_state.identity,
            )

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

        with self._lock:
            current_server = self._server_state.current_identity
            if current_server != basis.server:
                return AdbTransportListObservationServerConflict(
                    basis=basis,
                    transport_list=transport_list,
                    current_server=current_server,
                    state=self._transport_list_state.snapshot(),
                )
            expected = self._transport_list_state.snapshot()
            result = self._transport_list_state.observe(
                basis,
                transport_list,
                expected,
            )

        if isinstance(result, AdbTransportListObserved) and self._publisher is not None:
            self._publisher.publish(result)
        return result


__all__ = [
    "AdbTransportListCoordinator",
    "AdbTransportListCoordinatedObservationResult",
    "AdbTransportListObservationServerConflict",
]
