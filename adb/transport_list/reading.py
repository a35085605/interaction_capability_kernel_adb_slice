from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from adb.server.availability import AdbServerUnavailableError
from adb.server.identity import AdbServerIdentity
from adb.server.state import AdbServerStateView
from adb.transport_list.identity import AdbTransportListIdentity
from adb.transport_list.model import AdbTransportList
from adb.transport_list.observation import (
    AdbTransportListObservation,
    AdbTransportListObservationIdentifier,
)
from adb.transport_list.reader import AdbTransportListReader
from adb.transport_list.state import AdbTransportListState, AdbTransportListStateView


_RLockType = type(RLock())


@dataclass(frozen=True, slots=True)
class AdbTransportListReadBasis:
    """Authoritative runtime basis captured before one transport-list read."""

    server: AdbServerIdentity
    transport_list_state: AdbTransportListState

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if not isinstance(self.transport_list_state, AdbTransportListState):
            raise TypeError("transport_list_state must be AdbTransportListState")

    @property
    def transport_list_identity(self) -> AdbTransportListIdentity | None:
        """Retained transport-list identity watermark captured by this basis."""

        return self.transport_list_state.identity


@dataclass(frozen=True, slots=True)
class AdbTransportListRead:
    """An identified transport-list observation together with its pre-read basis."""

    basis: AdbTransportListReadBasis
    observation: AdbTransportListObservation

    def __post_init__(self) -> None:
        if not isinstance(self.basis, AdbTransportListReadBasis):
            raise TypeError("basis must be AdbTransportListReadBasis")
        if not isinstance(self.observation, AdbTransportListObservation):
            raise TypeError("observation must be AdbTransportListObservation")
        if self.observation.server != self.basis.server:
            raise ValueError("observation server must match read basis server")

    @property
    def identity(self) -> AdbTransportListIdentity:
        return self.observation.identity

    @property
    def server(self) -> AdbServerIdentity:
        return self.observation.server

    @property
    def transport_list(self) -> AdbTransportList:
        return self.observation.transport_list


class AdbTransportListReaderFacade:
    """Read and identify one transport list against a captured runtime basis.

    The authority lock is held only while the server and transport-list basis is captured.
    Raw ADB I/O and identity issuance happen after the lock is released. Consumers must
    validate the returned basis again when crossing the authoritative commit boundary.
    """

    def __init__(
        self,
        reader: AdbTransportListReader,
        *,
        server_state: AdbServerStateView,
        transport_list_state: AdbTransportListStateView,
        observation_identifier: AdbTransportListObservationIdentifier,
        authority_lock: _RLockType | None = None,
    ) -> None:
        if not callable(getattr(reader, "read", None)):
            raise TypeError("reader must satisfy AdbTransportListReader")
        if not isinstance(server_state, AdbServerStateView):
            raise TypeError("server_state must satisfy AdbServerStateView")
        if not isinstance(transport_list_state, AdbTransportListStateView):
            raise TypeError("transport_list_state must satisfy AdbTransportListStateView")
        if not isinstance(
            observation_identifier, AdbTransportListObservationIdentifier
        ):
            raise TypeError(
                "observation_identifier must be AdbTransportListObservationIdentifier"
            )
        if authority_lock is not None and not isinstance(authority_lock, _RLockType):
            raise TypeError("authority_lock must be a reentrant lock or None")

        self._reader = reader
        self._server_state = server_state
        self._transport_list_state = transport_list_state
        self._observation_identifier = observation_identifier
        self._lock = RLock() if authority_lock is None else authority_lock

    @property
    def reader(self) -> AdbTransportListReader:
        return self._reader

    @property
    def server_state(self) -> AdbServerStateView:
        return self._server_state

    @property
    def transport_list_state(self) -> AdbTransportListStateView:
        return self._transport_list_state

    @property
    def observation_identifier(self) -> AdbTransportListObservationIdentifier:
        return self._observation_identifier

    def read(self) -> AdbTransportListRead:
        """Capture basis, perform raw I/O unlocked, then identify the completed read."""

        with self._lock:
            server_state = self._server_state.snapshot()
            server = server_state.server
            endpoint = server_state.endpoint
            if server is None or endpoint is None:
                raise AdbServerUnavailableError(
                    "no authoritative ADB server is available for transport-list refresh"
                )
            basis = AdbTransportListReadBasis(
                server=server,
                transport_list_state=self._transport_list_state.snapshot(),
            )

        transport_list = self._reader.read(endpoint)
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport-list reader must return AdbTransportList")

        observation = self._observation_identifier.identify(server, transport_list)
        return AdbTransportListRead(basis=basis, observation=observation)


__all__ = [
    "AdbTransportListRead",
    "AdbTransportListReadBasis",
    "AdbTransportListReaderFacade",
]
