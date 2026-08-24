from __future__ import annotations

from adb.tracking.state import AdbDevicesWriter
from adb.tracking.signal import (
    AdbDevicesSnapshotObserved,
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from eventing import EventPublisher


class AdbDevicesStateBackedTrackingPublisher:
    """Commit tracking state before making accepted tracking signals observable.

    This establishes the invariant that every published snapshot observation has already been
    committed to the shared tracked-devices state for the same tracking scope.
    """

    def __init__(
        self,
        devices: AdbDevicesWriter,
        publisher: EventPublisher,
    ) -> None:
        if not isinstance(devices, AdbDevicesWriter):
            raise TypeError("devices must satisfy AdbDevicesWriter")
        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        self._devices = devices
        self._publisher = publisher

    def publish(self, event: object) -> None:
        accepted = True
        if isinstance(event, AdbDevicesTrackingStarted):
            accepted = self._devices.begin_tracking(event.scope)
        elif isinstance(event, AdbDevicesSnapshotObserved):
            accepted = self._devices.observe(event)
        elif isinstance(event, (AdbDevicesTrackingFailed, AdbDevicesTrackingStopped)):
            accepted = self._devices.end_tracking(event.scope)

        if accepted:
            self._publisher.publish(event)


__all__ = ["AdbDevicesStateBackedTrackingPublisher"]
