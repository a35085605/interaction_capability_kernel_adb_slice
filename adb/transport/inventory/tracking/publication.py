from __future__ import annotations

from adb.transport.inventory.state import AdbDevicesInventoryWriter
from adb.transport.inventory.tracking.signal import (
    AdbDevicesSnapshotObserved,
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from eventing import EventPublisher


class AdbDevicesStateBackedTrackingPublisher:
    """Commit tracking state before making accepted tracking signals observable.

    This establishes the invariant that every published snapshot observation has already been
    committed to the shared inventory state for the same tracking scope.
    """

    def __init__(
        self,
        inventory: AdbDevicesInventoryWriter,
        publisher: EventPublisher,
    ) -> None:
        if not isinstance(inventory, AdbDevicesInventoryWriter):
            raise TypeError("inventory must satisfy AdbDevicesInventoryWriter")
        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        self._inventory = inventory
        self._publisher = publisher

    def publish(self, event: object) -> None:
        accepted = True
        if isinstance(event, AdbDevicesTrackingStarted):
            accepted = self._inventory.begin_tracking(event.scope)
        elif isinstance(event, AdbDevicesSnapshotObserved):
            accepted = self._inventory.observe(event)
        elif isinstance(event, (AdbDevicesTrackingFailed, AdbDevicesTrackingStopped)):
            accepted = self._inventory.end_tracking(event.scope)

        if accepted:
            self._publisher.publish(event)


__all__ = ["AdbDevicesStateBackedTrackingPublisher"]
