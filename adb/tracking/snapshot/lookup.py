from __future__ import annotations

from typing import Protocol

from adb.server.identity import AdbServer
from adb.tracking.snapshot.identity import AdbDevicesSnapshot
from adb.aosp.tracking.model import AdbDevicesRecord, AdbTrackedDevice
from adb.tracking.snapshot.reader import AdbDevicesSnapshotReader
from adb.transport.selection import (
    AdbTransportById,
    AdbTransportBySerial,
    AdbTransportSelector,
)


class AdbTrackedDeviceLookup(Protocol):
    """Find one transport row in a fresh track-devices snapshot."""

    def find(
        self,
        server: AdbServer,
        selector: AdbTransportSelector,
    ) -> AdbTrackedDevice | None:
        ...


def find_tracked_device(
    record: AdbDevicesRecord,
    selector: AdbTransportSelector,
) -> AdbTrackedDevice | None:
    """Derive one observed transport row from a complete ADB devices record."""

    if not isinstance(record, AdbDevicesRecord):
        raise TypeError("record must be AdbDevicesRecord")
    if isinstance(selector, AdbTransportBySerial):
        matches = [
            device for device in record.devices if device.serial == selector.serial.value
        ]
    elif isinstance(selector, AdbTransportById):
        matches = [
            device
            for device in record.devices
            if device.transport_id == selector.transport_id.value
        ]
    else:
        raise TypeError("selector must be AdbTransportBySerial or AdbTransportById")

    if len(matches) > 1:
        raise ValueError("ADB transport selector matched multiple tracked-device rows")
    return matches[0] if matches else None


class SnapshotAdbTrackedDeviceLookup:
    """Single-row lookup over freshly identified track-devices snapshots."""

    def __init__(self, snapshot_reader: AdbDevicesSnapshotReader) -> None:
        if not callable(getattr(snapshot_reader, "read", None)):
            raise TypeError("snapshot_reader must provide read()")
        self.snapshot_reader = snapshot_reader

    def find(
        self,
        server: AdbServer,
        selector: AdbTransportSelector,
    ) -> AdbTrackedDevice | None:
        snapshot = self.snapshot_reader.read(server)
        if not isinstance(snapshot, AdbDevicesSnapshot):
            raise TypeError("snapshot reader must return AdbDevicesSnapshot")
        return find_tracked_device(snapshot.record, selector)


__all__ = [
    "AdbTrackedDeviceLookup",
    "SnapshotAdbTrackedDeviceLookup",
    "find_tracked_device",
]
