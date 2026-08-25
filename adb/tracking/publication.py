from __future__ import annotations

from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.identity import AdbServer
from adb.server.state import AdbServerStateView
from adb.tracking.snapshot.state import (
    AdbDevicesObservation,
    AdbDevicesSnapshotView,
    AdbDevicesSnapshotWriter,
)
from adb.tracking.signal import (
    AdbDevicesSnapshotObserved,
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from eventing import EventPublisher


@runtime_checkable
class _AdbDevicesSnapshotStateAccess(
    AdbDevicesSnapshotView,
    AdbDevicesSnapshotWriter,
    Protocol,
):
    """Read and commit authoritative tracked-devices snapshot state."""


class AdbDevicesSnapshotStateBackedTrackingPublisher:
    """Commit current-server tracking observations into snapshot state before publication.

    ``AdbServerStateView`` remains the current-lifetime authority. Accepted snapshots are stored
    as server-bound ``AdbDevicesObservation`` values so downstream readers retain provenance
    without treating the observation's server as current-server truth.
    """

    def __init__(
        self,
        devices: _AdbDevicesSnapshotStateAccess,
        server_state: AdbServerStateView,
        publisher: EventPublisher,
    ) -> None:
        if not isinstance(devices, _AdbDevicesSnapshotStateAccess):
            raise TypeError(
                "devices must satisfy AdbDevicesSnapshotView and AdbDevicesSnapshotWriter"
            )
        if not isinstance(server_state, AdbServerStateView):
            raise TypeError("server_state must satisfy AdbServerStateView")
        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        self._devices = devices
        self._server_state = server_state
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
        """End observation for one server without changing the last committed observation."""

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
            current = self._devices.current
            if current is not None and current.server != server:
                self._devices.invalidate_current()
            self._active_server = server
            return True

    def _observe(self, event: AdbDevicesSnapshotObserved) -> bool:
        with self._lock:
            if event.server != self._active_server:
                return False
            if self._server_state.current != event.server:
                return False
            return self._devices.observe(
                AdbDevicesObservation(event.server, event.snapshot)
            )

    @staticmethod
    def _require_server(server: AdbServer) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")


__all__ = ["AdbDevicesSnapshotStateBackedTrackingPublisher"]
