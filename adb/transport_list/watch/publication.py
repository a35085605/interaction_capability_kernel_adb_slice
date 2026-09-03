from __future__ import annotations

from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.identity import AdbServerIdentity
from adb.server.state import AdbServerStateView
from adb.transport_list.coordinator import AdbTransportListObservationCoordinator
from adb.transport_list.identity import AdbTransportListIdentityIssuer
from adb.transport_list.state import (
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
    """Publish watch signals after authoritative observation coordination.

    Watch lifetime remains local to this adapter. Server provenance, revision identity issuance, and
    transport-list state commits are delegated to the shared observation coordinator so read- and
    watch-produced authoritative observations use one arbitration boundary.

    The legacy state/server/issuer constructor remains supported for standalone callers. Runtime
    composition should inject ``coordinator`` so all authoritative producers share one boundary.
    """

    def __init__(
        self,
        transport_list_state: _AdbTransportListStateAccess | None = None,
        server_state: AdbServerStateView | None = None,
        identity_issuer: AdbTransportListIdentityIssuer | None = None,
        publisher: EventPublisher | None = None,
        *,
        coordinator: AdbTransportListObservationCoordinator | None = None,
    ) -> None:
        if coordinator is None:
            if not isinstance(transport_list_state, _AdbTransportListStateAccess):
                raise TypeError(
                    "transport_list_state must satisfy AdbTransportListStateView and "
                    "AdbTransportListStateWriter"
                )
            if not isinstance(server_state, AdbServerStateView):
                raise TypeError("server_state must satisfy AdbServerStateView")
            if not isinstance(identity_issuer, AdbTransportListIdentityIssuer):
                raise TypeError("identity_issuer must be AdbTransportListIdentityIssuer")
            coordinator = AdbTransportListObservationCoordinator(
                transport_list_state,
                server_state,
                identity_issuer,
            )
        else:
            if not isinstance(coordinator, AdbTransportListObservationCoordinator):
                raise TypeError("coordinator must be AdbTransportListObservationCoordinator")
            if any(
                value is not None
                for value in (transport_list_state, server_state, identity_issuer)
            ):
                raise ValueError(
                    "transport-list state, server state, and identity issuer must be omitted "
                    "when coordinator is provided"
                )
        if publisher is None or not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        self._coordinator = coordinator
        self._publisher = publisher
        self._lock = Lock()
        self._active_server: AdbServerIdentity | None = None

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
            if self._active_server == server:
                return True
            if not self._coordinator.prepare_server(server):
                return False
            self._active_server = server
            return True

    def _observe(self, event: AdbTransportListWatchObservation) -> bool:
        with self._lock:
            if event.server != self._active_server:
                return False
            result = self._coordinator.observe(event.server, event.transport_list)
            return isinstance(result, AdbTransportListObserved)

    @staticmethod
    def _require_server(server: AdbServerIdentity) -> None:
        if not isinstance(server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")


__all__ = ["AdbTransportListStateBackedWatchPublisher"]
