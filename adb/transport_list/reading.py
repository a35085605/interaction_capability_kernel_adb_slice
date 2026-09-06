from __future__ import annotations

from threading import RLock

from adb.server.availability import AdbServerUnavailableError
from adb.server.state import AdbServerStateView
from adb.transport_list.model import AdbTransportList
from adb.transport_list.observation import (
    AdbTransportListObservation,
    AdbTransportListObservationBasis,
    AdbTransportListObservationIdentifier,
)
from adb.transport_list.reader import AdbTransportListReader
from adb.transport_list.state import AdbTransportListState, AdbTransportListStateView


_RLockType = type(RLock())


class AdbTransportListReaderFacade:
    """Read and identify one transport list against a captured runtime basis.

    The authority lock is held only while the server and transport-list identities are captured.
    Raw ADB I/O and identity issuance happen after the lock is released. Consumers validate the
    returned observation basis again when crossing the authoritative commit boundary.
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

    def read(self) -> AdbTransportListObservation:
        """Capture basis, perform raw I/O unlocked, then identify the completed observation."""

        observation, _ = self._read_with_state_fence()
        return observation

    def _read_with_state_fence(
        self,
    ) -> tuple[AdbTransportListObservation, AdbTransportListState]:
        """Internal phase-one bridge retaining the pre-existing full-state commit fence."""

        with self._lock:
            server_state = self._server_state.snapshot()
            server = server_state.server
            endpoint = server_state.endpoint
            if server is None or endpoint is None:
                raise AdbServerUnavailableError(
                    "no authoritative ADB server is available for transport-list refresh"
                )
            state_fence = self._transport_list_state.snapshot()
            basis = AdbTransportListObservationBasis(
                server=server,
                transport_list_identity=state_fence.identity,
            )

        transport_list = self._reader.read(endpoint)
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport-list reader must return AdbTransportList")

        observation = self._observation_identifier.identify(basis, transport_list)
        return observation, state_fence


__all__ = ["AdbTransportListReaderFacade"]
