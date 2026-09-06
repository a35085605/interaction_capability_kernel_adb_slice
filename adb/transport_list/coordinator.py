from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Protocol, TypeAlias, runtime_checkable

from adb.server.availability import AdbServerUnavailableError
from adb.server.identity import AdbServerIdentity
from adb.server.state import AdbServerStateView
from adb.transport_list.model import AdbTransportList
from adb.transport_list.observation import (
    AdbTransportListObservation,
    AdbTransportListObservationIdentifier,
)
from adb.transport_list.reader import AdbTransportListReader
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
    """Evidence that an observation belongs to a non-authoritative server lifetime."""

    observation: AdbTransportListObservation
    current_server: AdbServerIdentity | None
    state: AdbTransportListState

    def __post_init__(self) -> None:
        if not isinstance(self.observation, AdbTransportListObservation):
            raise TypeError("observation must be AdbTransportListObservation")
        if self.current_server is not None and not isinstance(
            self.current_server, AdbServerIdentity
        ):
            raise TypeError("current_server must be AdbServerIdentity or None")
        if not isinstance(self.state, AdbTransportListState):
            raise TypeError("state must be AdbTransportListState")
        if self.current_server == self.observation.server:
            raise ValueError("server conflict requires a different authoritative server")

    @property
    def server(self) -> AdbServerIdentity:
        return self.observation.server

    def __bool__(self) -> bool:
        return False


AdbTransportListCoordinatedObservationResult: TypeAlias = (
    AdbTransportListObservationResult | AdbTransportListObservationServerConflict
)


class AdbTransportListCoordinator:
    """Coordinate identified transport-list observations with runtime server authority."""

    def __init__(
        self,
        transport_list_state: _AdbTransportListStateAccess,
        server_state: AdbServerStateView,
        *,
        observation_identifier: AdbTransportListObservationIdentifier | None = None,
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
        if observation_identifier is None:
            observation_identifier = AdbTransportListObservationIdentifier(
                after=transport_list_state.identity
            )
        if not isinstance(
            observation_identifier, AdbTransportListObservationIdentifier
        ):
            raise TypeError(
                "observation_identifier must be "
                "AdbTransportListObservationIdentifier or None"
            )
        if publisher is not None and not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher or be None")
        if authority_lock is not None and not isinstance(authority_lock, _RLockType):
            raise TypeError("authority_lock must be a reentrant lock or None")
        self._transport_list_state = transport_list_state
        self._server_state = server_state
        self._observation_identifier = observation_identifier
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

    @property
    def observation_identifier(self) -> AdbTransportListObservationIdentifier:
        """Runtime-scoped identifier shared by all transport-list observation producers."""

        return self._observation_identifier

    def refresh(
        self,
        reader: AdbTransportListReader,
    ) -> AdbTransportListCoordinatedObservationResult:
        """Read, identify, and conditionally commit a transport-list refresh.

        The refresh keeps its pre-read server and state fences, so newer watch observations
        retain authority over an in-flight read. Identity issuance happens after the raw read
        completes and before the observation crosses the runtime authority boundary.
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
            expected = self._transport_list_state.snapshot()

        transport_list = reader.read(endpoint)
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport-list reader must return AdbTransportList")
        observation = self._observation_identifier.identify(server, transport_list)
        return self.observe(observation, expected=expected)

    def observe(
        self,
        observation: AdbTransportListObservation,
        *,
        expected: AdbTransportListState | None = None,
    ) -> AdbTransportListCoordinatedObservationResult:
        """Commit an identified observation while its server and state fences remain valid.

        ``expected`` preserves the state basis of a one-shot refresh; stream observations
        may omit it to linearize against state at commit time.
        """

        if not isinstance(observation, AdbTransportListObservation):
            raise TypeError("observation must be AdbTransportListObservation")
        if not self._observation_identifier.owns(observation):
            raise ValueError("observation identity belongs to a different runtime scope")
        if expected is not None and not isinstance(expected, AdbTransportListState):
            raise TypeError("expected must be AdbTransportListState or None")

        with self._lock:
            current_server = self._server_state.current_identity
            if current_server != observation.server:
                return AdbTransportListObservationServerConflict(
                    observation=observation,
                    current_server=current_server,
                    state=self._transport_list_state.snapshot(),
                )
            commit_expected = (
                self._transport_list_state.snapshot() if expected is None else expected
            )
            result = self._transport_list_state.observe(observation, commit_expected)

        if isinstance(result, AdbTransportListObserved) and self._publisher is not None:
            self._publisher.publish(result)
        return result


__all__ = [
    "AdbTransportListCoordinator",
    "AdbTransportListCoordinatedObservationResult",
    "AdbTransportListObservationServerConflict",
]
