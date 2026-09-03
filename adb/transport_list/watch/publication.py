from __future__ import annotations

from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.identity import AdbServerIdentity
from adb.server.state import AdbServerStateView
from adb.transport_list.identity import AdbTransportListIdentityIssuer
from adb.transport_list.revision import AdbTransportListRevision
from adb.transport_list.state import (
    AdbTransportListInvalidated,
    AdbTransportListObserved,
    AdbTransportListStateView,
    AdbTransportListStateWriter,
)
from adb.transport_list.watch.signal import (
    AdbTransportListWatchObservation,
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
    """Read and commit authoritative transport-list state."""


class AdbTransportListStateBackedWatchPublisher:
    """Commit current-server transport lists into state before publication.

    Server provenance remains a watch-lifecycle concern. The committed transport-list state
    contains only the transport-list revision, its identity, and visibility status.
    """

    def __init__(
        self,
        transport_list_state: _AdbTransportListStateAccess,
        server_state: AdbServerStateView,
        identity_issuer: AdbTransportListIdentityIssuer,
        publisher: EventPublisher,
    ) -> None:
        if not isinstance(transport_list_state, _AdbTransportListStateAccess):
            raise TypeError(
                "transport_list_state must satisfy AdbTransportListStateView and "
                "AdbTransportListStateWriter"
            )
        if not isinstance(server_state, AdbServerStateView):
            raise TypeError("server_state must satisfy AdbServerStateView")
        if not isinstance(identity_issuer, AdbTransportListIdentityIssuer):
            raise TypeError("identity_issuer must be AdbTransportListIdentityIssuer")
        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        self._transport_list_state = transport_list_state
        self._server_state = server_state
        self._identity_issuer = identity_issuer
        self._publisher = publisher
        self._lock = Lock()
        self._active_server: AdbServerIdentity | None = None
        self._committed_server: AdbServerIdentity | None = None

    def publish(self, event: object) -> None:
        accepted = True
        if isinstance(event, AdbTransportListWatchStarted):
            accepted = self._begin_watch(event.server)
        elif isinstance(event, AdbTransportListWatchObservation):
            accepted = self._observe(event)
        elif isinstance(event, (AdbTransportListWatchFailed, AdbTransportListWatchStopped)):
            accepted = self.end_watch(event.server)

        if accepted:
            self._publisher.publish(event)

    def end_watch(self, server: AdbServerIdentity) -> bool:
        """End the watch for one server while preserving the last committed transport list."""

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
            if state.current is not None and self._committed_server != server:
                identity = state.current_identity
                assert identity is not None
                invalidation = self._transport_list_state.invalidate(identity)
                if not isinstance(invalidation, AdbTransportListInvalidated):
                    return False
            self._active_server = server
            return True

    def _observe(self, event: AdbTransportListWatchObservation) -> bool:
        with self._lock:
            if event.server != self._active_server:
                return False
            if self._server_state.current != event.server:
                return False
            expected = self._transport_list_state.snapshot()
            revision = AdbTransportListRevision(
                identity=self._identity_issuer.issue(),
                transport_list=event.transport_list,
            )
            result = self._transport_list_state.observe(revision, expected)
            if not isinstance(result, AdbTransportListObserved):
                return False
            self._committed_server = event.server
            return True

    @staticmethod
    def _require_server(server: AdbServerIdentity) -> None:
        if not isinstance(server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")


__all__ = ["AdbTransportListStateBackedWatchPublisher"]
