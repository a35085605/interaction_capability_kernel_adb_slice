from __future__ import annotations

from threading import Lock

from adb.server.identity import AdbServer
from adb.server.state import AdbServerStateView
from adb.tracking.snapshot.state import AdbDevicesSnapshotWriter
from adb.tracking.signal import (
    AdbDevicesSnapshotObserved,
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from eventing import EventPublisher


class AdbDevicesSnapshotStateBackedTrackingPublisher:
    """Commit current-server tracking observations into snapshot state before publication.

    ``AdbServerStateView`` is the authoritative server-lifetime gate. Tracking signals are
    accepted only while their exact ``AdbServer`` is current; snapshot state therefore needs to
    understand only runtime-scoped snapshot identity, not ``ServerEpoch``.
    """

    def __init__(
        self,
        devices: AdbDevicesSnapshotWriter,
        server_state: AdbServerStateView,
        publisher: EventPublisher,
    ) -> None:
        if not isinstance(devices, AdbDevicesSnapshotWriter):
            raise TypeError("devices must satisfy AdbDevicesSnapshotWriter")
        if not isinstance(server_state, AdbServerStateView):
            raise TypeError("server_state must satisfy AdbServerStateView")
        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        self._devices = devices
        self._server_state = server_state
        self._publisher = publisher
        self._lock = Lock()
        self._active_server: AdbServer | None = None
        self._snapshot_server: AdbServer | None = server_state.current

    def publish(self, event: object) -> None:
        accepted = True
        if isinstance(event, AdbDevicesTrackingStarted):
            accepted = self._begin_tracking(event.server)
        elif isinstance(event, AdbDevicesSnapshotObserved):
            accepted = self._observe(event)
        elif isinstance(event, (AdbDevicesTrackingFailed, AdbDevicesTrackingStopped)):
            accepted = self.end_tracking(event.server)

        if accepted:
            self._publisher.publish(event)

    def end_tracking(self, server: AdbServer) -> bool:
        """End observation for one server without changing the last committed snapshot."""

        self._require_server(server)
        with self._lock:
            if server != self._active_server:
                return False
            self._active_server = None
            return True

    def _begin_tracking(self, server: AdbServer) -> bool:
        self._require_server(server)
        with self._lock:
            if self._server_state.current != server:
                return False
            if self._active_server == server:
                return True
            if self._snapshot_server != server:
                self._devices.invalidate_current()
                self._snapshot_server = server
            self._active_server = server
            return True

    def _observe(self, event: AdbDevicesSnapshotObserved) -> bool:
        with self._lock:
            if event.server != self._active_server:
                return False
            if self._server_state.current != event.server:
                return False
            return self._devices.observe(event.snapshot)

    @staticmethod
    def _require_server(server: AdbServer) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")


__all__ = ["AdbDevicesSnapshotStateBackedTrackingPublisher"]
