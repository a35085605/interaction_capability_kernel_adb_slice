from __future__ import annotations

from typing import Protocol

from adb.aosp.tracking.model import Device
from adb.server.lifetime import AdbServerLifetime
from adb.tracking.snapshot.identity import AdbDevicesSnapshot
from adb.tracking.snapshot.reader import AdbDevicesSnapshotReader
from adb.transport.selection import (
    AdbTransportById,
    AdbTransportBySerial,
    AdbTransportSelector,
)


class AdbTrackedDeviceLookup(Protocol):
    """Find one AOSP device row in a fresh domain-identified snapshot."""

    def find(
        self,
        server: AdbServerLifetime,
        selector: AdbTransportSelector,
    ) -> Device | None:
        ...


def find_tracked_device(
    snapshot: AdbDevicesSnapshot,
    selector: AdbTransportSelector,
) -> Device | None:
    """Select one AOSP ``Device`` evidence row from a domain snapshot."""

    if not isinstance(snapshot, AdbDevicesSnapshot):
        raise TypeError("snapshot must be AdbDevicesSnapshot")
    if isinstance(selector, AdbTransportBySerial):
        matches = [
            device
            for device in snapshot.payload.devices
            if device.serial == selector.serial.value
        ]
    elif isinstance(selector, AdbTransportById):
        matches = [
            device
            for device in snapshot.payload.devices
            if device.transport_id == selector.transport_id.value
        ]
    else:
        raise TypeError("selector must be AdbTransportBySerial or AdbTransportById")

    if len(matches) > 1:
        raise ValueError("ADB transport selector matched multiple AOSP device rows")
    return matches[0] if matches else None


class SnapshotAdbTrackedDeviceLookup:
    """Single-row lookup over freshly identified track-devices snapshots."""

    def __init__(self, snapshot_reader: AdbDevicesSnapshotReader) -> None:
        if not callable(getattr(snapshot_reader, "read", None)):
            raise TypeError("snapshot_reader must provide read()")
        self.snapshot_reader = snapshot_reader

    def find(
        self,
        server: AdbServerLifetime,
        selector: AdbTransportSelector,
    ) -> Device | None:
        if not isinstance(server, AdbServerLifetime):
            raise TypeError("server must be AdbServerLifetime")
        snapshot = self.snapshot_reader.read(server.endpoint)
        if not isinstance(snapshot, AdbDevicesSnapshot):
            raise TypeError("snapshot reader must return AdbDevicesSnapshot")
        return find_tracked_device(snapshot, selector)


__all__ = [
    "AdbTrackedDeviceLookup",
    "SnapshotAdbTrackedDeviceLookup",
    "find_tracked_device",
]
