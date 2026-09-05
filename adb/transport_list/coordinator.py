from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Protocol, TypeAlias, runtime_checkable

from adb.server.availability import AdbServerUnavailableError
from adb.server.identity import AdbServerIdentity
from adb.server.state import AdbServerStateView
from adb.transport_list.model import AdbTransportList
from adb.transport_list.reader import AdbTransportListReader
from adb.transport_list.state import (
    AdbTransportListInvalidated,
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
    """Evidence that an observation belongs to a non-authoritative server lifetime."""

    server: AdbServerIdentity
    current_server: AdbServerIdentity | None
    state: AdbTransportListState

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if self.current_server is not None and not isinstance(
            self.current_server, AdbServerIdentity
        ):
            raise TypeError("current_server must be AdbServerIdentity or None")
        if not isinstance(self.state, AdbTransportListState):
            raise TypeError("state must be AdbTransportListState")
        if self.current_server == self.server:
            raise ValueError("server conflict requires a different authoritative server")

    def __bool__(self) -> bool:
        return False


AdbTransportListCoordinatedObservationResult: TypeAlias = (
    AdbTransportListObservationResult | AdbTransportListObservationServerConflict
)


class AdbTransportListCoordinator:
    """Coordinate transport-list observations with runtime server authority.

    Validates server provenance and coordinates accepted observations with the authoritative
    transport-list state store.
    """

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
        self._committed_server: AdbServerIdentity | None = None

    @property
    def server_state(self) -> AdbServerStateView:
        """Server authority used to fence transport-list observations."""

        return self._server_state

    @property
    def transport_list_state(self) -> _AdbTransportListStateAccess:
        """Authoritative transport-list state committed by this coordinator."""

        return self._transport_list_state

    def refresh(
        self,
        reader: AdbTransportListReader,
    ) -> AdbTransportListCoordinatedObservationResult:
        """Read and conditionally commit a transport-list refresh.

        The refresh keeps its pre-read server and state fences, so newer watch observations
        retain authority over an in-flight read.
        """

        if not callable(getattr(reader, "read", None)):
            raise TypeError("reader must satisfy AdbTransportListReader")

        with self._lock:
            server_state = self._server_state.snapshot()
            server = server_state.server
            endpoint = server_state.endpoint
            if server is None or endpoint is None:
                raise AdbServerUnavailableError(
                    "no authoritative ADB server is available for transport-list refresh"
                )
            if not self._prepare_server_locked(server):
                raise RuntimeError(
                    "authoritative ADB server changed while preparing transport-list refresh"
                )
            expected = self._transport_list_state.snapshot()

        transport_list = reader.read(endpoint)
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport-list reader must return AdbTransportList")
        return self.observe(server, transport_list, expected=expected)

    def prepare_server(self, server: AdbServerIdentity) -> bool:
        """Align transport-list visibility with ``server``.

        On success, any visible transport list belongs to ``server`` and stale lifetime
        evidence has been invalidated. Returns ``False`` when ``server`` has lost authority.
        """

        self._require_server(server)
        with self._lock:
            return self._prepare_server_locked(server)

    def _prepare_server_locked(self, server: AdbServerIdentity) -> bool:
        if self._server_state.current_identity != server:
            return False
        state = self._transport_list_state.snapshot()
        if state.current is None or self._committed_server == server:
            return True
        identity = state.current_identity
        assert identity is not None
        invalidation = self._transport_list_state.invalidate(identity)
        return isinstance(invalidation, AdbTransportListInvalidated)

    def observe(
        self,
        server: AdbServerIdentity,
        transport_list: AdbTransportList,
        *,
        expected: AdbTransportListState | None = None,
    ) -> AdbTransportListCoordinatedObservationResult:
        """Commit a complete observation while its server and state fences remain valid.

        ``expected`` preserves the state basis of a one-shot refresh; stream observations
        may omit it to linearize against state at commit time.
        """

        self._require_server(server)
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")
        if expected is not None and not isinstance(expected, AdbTransportListState):
            raise TypeError("expected must be AdbTransportListState or None")

        with self._lock:
            current_server = self._server_state.current_identity
            if current_server != server:
                return AdbTransportListObservationServerConflict(
                    server=server,
                    current_server=current_server,
                    state=self._transport_list_state.snapshot(),
                )
            commit_expected = (
                self._transport_list_state.snapshot() if expected is None else expected
            )
            result = self._transport_list_state.observe(transport_list, commit_expected)
            if result:
                self._committed_server = server

        if isinstance(result, AdbTransportListObserved) and self._publisher is not None:
            self._publisher.publish(result)
        return result

    @staticmethod
    def _require_server(server: AdbServerIdentity) -> None:
        if not isinstance(server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")


__all__ = [
    "AdbTransportListCoordinator",
    "AdbTransportListCoordinatedObservationResult",
    "AdbTransportListObservationServerConflict",
]
