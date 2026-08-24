from __future__ import annotations

from threading import Lock

from adb.server.identity import ServerEpoch
from adb.tracking.identity import AdbDevicesTrackingScope, DevicesTrackingEpoch
from adb.tracking.state import AdbDevicesSnapshotWriter
from adb.tracking.signal import (
    AdbDevicesSnapshotObserved,
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from eventing import EventPublisher


def _scope_order(
    scope: AdbDevicesTrackingScope,
) -> tuple[ServerEpoch, DevicesTrackingEpoch]:
    return scope.server_epoch, scope.epoch


class AdbDevicesSnapshotStateBackedTrackingPublisher:
    """Fence tracking sessions and commit clean snapshot state before publication.

    ``DevicesTrackingEpoch`` remains local to producer/session correlation. Once a snapshot is
    accepted for the active scope, only its ``ServerEpoch`` and value enter
    ``AdbDevicesSnapshotState``, which assigns an independent snapshot epoch.
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
        self._active_scope: AdbDevicesTrackingScope | None = None
        self._latest_scope: AdbDevicesTrackingScope | None = None

    def publish(self, event: object) -> None:
        accepted = True
        if isinstance(event, AdbDevicesTrackingStarted):
            accepted = self._begin_tracking(event.scope)
        elif isinstance(event, AdbDevicesSnapshotObserved):
            accepted = self._observe(event)
        elif isinstance(event, (AdbDevicesTrackingFailed, AdbDevicesTrackingStopped)):
            accepted = self.end_tracking(event.scope)

        if accepted:
            self._publisher.publish(event)

    def end_tracking(self, scope: AdbDevicesTrackingScope) -> bool:
        """End one exact producer scope without changing the last committed snapshot."""

        self._require_scope(scope)
        with self._lock:
            if scope != self._active_scope:
                return False
            self._active_scope = None
            return True

    def _begin_tracking(self, scope: AdbDevicesTrackingScope) -> bool:
        self._require_scope(scope)
        with self._lock:
            if self._active_scope == scope:
                return True
            latest = self._latest_scope
            if latest is not None and _scope_order(scope) <= _scope_order(latest):
                return False
            if not self._devices.advance_server(scope.server_epoch):
                return False
            self._latest_scope = scope
            self._active_scope = scope
            return True

    def _observe(self, event: AdbDevicesSnapshotObserved) -> bool:
        with self._lock:
            if event.scope != self._active_scope:
                return False
            return self._devices.observe(event.server_epoch, event.snapshot) is not None

    @staticmethod
    def _require_scope(scope: AdbDevicesTrackingScope) -> None:
        if not isinstance(scope, AdbDevicesTrackingScope):
            raise TypeError("scope must be AdbDevicesTrackingScope")


__all__ = ["AdbDevicesSnapshotStateBackedTrackingPublisher"]
