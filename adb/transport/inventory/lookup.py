from __future__ import annotations

from typing import Protocol

from adb.server.endpoint import AdbServerEndpoint
from adb.transport.inventory.model import AdbDevicesSnapshot, AdbTrackedDevice
from adb.transport.inventory.reader import (
    AdbDevicesSnapshotReader,
    SmartSocketAdbDevicesSnapshotReader,
)
from adb.transport.selection import (
    AdbTransportById,
    AdbTransportBySerial,
    AdbTransportSelector,
)


class AdbTrackedDeviceLookup(Protocol):
    """Find one observed transport row from a fresh complete inventory snapshot."""

    def find(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
    ) -> AdbTrackedDevice | None:
        ...


def find_tracked_device(
    snapshot: AdbDevicesSnapshot,
    selector: AdbTransportSelector,
) -> AdbTrackedDevice | None:
    """Derive one observed transport row from a complete ADB inventory snapshot."""

    if not isinstance(snapshot, AdbDevicesSnapshot):
        raise TypeError("snapshot must be AdbDevicesSnapshot")
    if isinstance(selector, AdbTransportBySerial):
        matches = [
            device for device in snapshot.devices if device.serial == selector.serial.value
        ]
    elif isinstance(selector, AdbTransportById):
        matches = [
            device
            for device in snapshot.devices
            if device.transport_id == selector.transport_id
        ]
    else:
        raise TypeError("selector must be AdbTransportBySerial or AdbTransportById")

    if len(matches) > 1:
        raise ValueError("ADB transport selector matched multiple inventory rows")
    return matches[0] if matches else None


class SnapshotAdbTrackedDeviceLookup:
    """Derived single-row lookup over a fresh complete transport-inventory snapshot."""

    def __init__(
        self,
        snapshot_reader: AdbDevicesSnapshotReader | None = None,
    ) -> None:
        if snapshot_reader is None:
            snapshot_reader = SmartSocketAdbDevicesSnapshotReader()
        if not callable(getattr(snapshot_reader, "read", None)):
            raise TypeError("snapshot_reader must provide read()")
        self.snapshot_reader = snapshot_reader

    def find(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
    ) -> AdbTrackedDevice | None:
        snapshot = self.snapshot_reader.read(endpoint)
        if not isinstance(snapshot, AdbDevicesSnapshot):
            raise TypeError("snapshot reader must return AdbDevicesSnapshot")
        return find_tracked_device(snapshot, selector)


__all__ = [
    "AdbTrackedDeviceLookup",
    "SnapshotAdbTrackedDeviceLookup",
    "find_tracked_device",
]
