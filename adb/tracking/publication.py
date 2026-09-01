from __future__ import annotations

from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.lifetime import AdbServerLifetime
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
    """Commit current-server tracking observations into snapshot state before publication while
    retaining server provenance.
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
        self._active_server: AdbServerLifetime | None = None

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

    def end_tracking(self, server: AdbServerLifetime) -> bool:
        """End tracking for one server while preserving the last committed observation."""

        self._require_server(server)
        with self._lock:
            if server != self._active_server:
                return False
            self._active_server = None
            return True

    def _begin_tracking(self, server: AdbServerLifetime) -> bool:
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
    def _require_server(server: AdbServerLifetime) -> None:
        if not isinstance(server, AdbServerLifetime):
            raise TypeError("server must be AdbServerLifetime")


__all__ = ["AdbDevicesSnapshotStateBackedTrackingPublisher"]
