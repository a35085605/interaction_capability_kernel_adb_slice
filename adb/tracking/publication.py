from __future__ import annotations

from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.identity import AdbServerIdentity
from adb.server.state import AdbServerStateView
from adb.tracking.snapshot.state import (
    AdbTransportListInvalidated,
    AdbTransportListObservation,
    AdbTransportListObserved,
    AdbTransportListStateView,
    AdbTransportListStateWriter,
)
from adb.tracking.signal import (
    AdbTransportListSnapshotObserved,
    AdbTransportListWatchFailed,
    AdbTransportListWatchStarted,
    AdbTransportListWatchStopped,
)
from eventing import EventPublisher


@runtime_checkable
class _AdbTransportListStateAccess(
    AdbTransportListStateView,
    AdbTransportListStateWriter,
    Protocol,
):
    """Read and commit authoritative transport-list snapshot state."""


class AdbTransportListStateBackedWatchPublisher:
    """Commit current-server transport-list observations into snapshot state before publication
    while retaining server provenance.
    """

    def __init__(
        self,
        transport_list_state: _AdbTransportListStateAccess,
        server_state: AdbServerStateView,
        publisher: EventPublisher,
    ) -> None:
        if not isinstance(transport_list_state, _AdbTransportListStateAccess):
            raise TypeError(
                "transport_list_state must satisfy AdbTransportListStateView and "
                "AdbTransportListStateWriter"
            )
        if not isinstance(server_state, AdbServerStateView):
            raise TypeError("server_state must satisfy AdbServerStateView")
        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        self._transport_list_state = transport_list_state
        self._server_state = server_state
        self._publisher = publisher
        self._lock = Lock()
        self._active_server: AdbServerIdentity | None = None

    def publish(self, event: object) -> None:
        accepted = True
        if isinstance(event, AdbTransportListWatchStarted):
            accepted = self._begin_watch(event.server)
        elif isinstance(event, AdbTransportListSnapshotObserved):
            accepted = self._observe(event)
        elif isinstance(event, (AdbTransportListWatchFailed, AdbTransportListWatchStopped)):
            accepted = self.end_watch(event.server)

        if accepted:
            self._publisher.publish(event)

    def end_watch(self, server: AdbServerIdentity) -> bool:
        """End the watch for one server while preserving the last committed observation."""

        self._require_server(server)
        with self._lock:
            if server != self._active_server:
                return False
            self._active_server = None
            return True

    def _begin_watch(self, server: AdbServerIdentity) -> bool:
        self._require_server(server)
        with self._lock:
            if self._server_state.current != server:
                return False
            if self._active_server == server:
                return True
            state = self._transport_list_state.snapshot()
            current = state.current
            if current is not None and current.server != server:
                invalidation = self._transport_list_state.invalidate(state)
                if not isinstance(invalidation, AdbTransportListInvalidated):
                    return False
            self._active_server = server
            return True

    def _observe(self, event: AdbTransportListSnapshotObserved) -> bool:
        with self._lock:
            if event.server != self._active_server:
                return False
            if self._server_state.current != event.server:
                return False
            observation = AdbTransportListObservation(event.server, event.snapshot)
            result = self._transport_list_state.observe(observation)
            return isinstance(result, AdbTransportListObserved)

    @staticmethod
    def _require_server(server: AdbServerIdentity) -> None:
        if not isinstance(server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")


__all__ = ["AdbTransportListStateBackedWatchPublisher"]
