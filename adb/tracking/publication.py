from __future__ import annotations

from threading import Lock

from adb.server.identity import AdbServer
from adb.tracking.state import AdbDevicesSnapshotWriter
from adb.tracking.signal import (
    AdbDevicesSnapshotObserved,
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from eventing import EventPublisher


class AdbDevicesSnapshotStateBackedTrackingPublisher:
    """Commit tracking observations into snapshot state before publication.

    Tracking signals are correlated by the ``AdbServer`` lifetime they observe. Snapshot state
    owns its independent ``AdbDevicesSnapshotEpoch`` revision sequence and rejects writes from
    older server epochs.
    """

    def __init__(
        self,
        devices: AdbDevicesSnapshotWriter,
        publisher: EventPublisher,
    ) -> None:
        if not isinstance(devices, AdbDevicesSnapshotWriter):
            raise TypeError("devices must satisfy AdbDevicesSnapshotWriter")
        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        self._devices = devices
        self._publisher = publisher
        self._lock = Lock()
        self._active_server: AdbServer | None = None

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
            if self._active_server == server:
                return True
            if not self._devices.advance_server(server.epoch):
                return False
            self._active_server = server
            return True

    def _observe(self, event: AdbDevicesSnapshotObserved) -> bool:
        with self._lock:
            if event.server != self._active_server:
                return False
            return self._devices.observe(event.server_epoch, event.snapshot) is not None

    @staticmethod
    def _require_server(server: AdbServer) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")


__all__ = ["AdbDevicesSnapshotStateBackedTrackingPublisher"]
