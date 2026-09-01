from __future__ import annotations

from typing import Protocol

from adb.server.lifetime import AdbServerLifetime
from adb.tracking.observation import AdbTrackedTransportObservation
from adb.tracking.snapshot.identity import AdbDevicesSnapshot
from adb.tracking.snapshot.reader import AdbDevicesSnapshotReader
from adb.transport.selection import (
    AdbTransportById,
    AdbTransportBySerial,
    AdbTransportSelector,
)


class AdbTrackedDeviceLookup(Protocol):
    """Find one tracked transport observation in a fresh domain snapshot."""

    def find(
        self,
        server: AdbServerLifetime,
        selector: AdbTransportSelector,
    ) -> AdbTrackedTransportObservation | None:
        ...


def find_tracked_device(
    snapshot: AdbDevicesSnapshot,
    selector: AdbTransportSelector,
) -> AdbTrackedTransportObservation | None:
    """Select one domain transport observation from a tracked-devices snapshot."""

    if not isinstance(snapshot, AdbDevicesSnapshot):
        raise TypeError("snapshot must be AdbDevicesSnapshot")
    if isinstance(selector, AdbTransportBySerial):
        matches = [
            observation
            for observation in snapshot.observations
            if observation.matches_serial(selector.serial)
        ]
    elif isinstance(selector, AdbTransportById):
        matches = [
            observation
            for observation in snapshot.observations
            if observation.transport_id == selector.transport_id
        ]
    else:
        raise TypeError("selector must be AdbTransportBySerial or AdbTransportById")

    if len(matches) > 1:
        raise ValueError("ADB transport selector matched multiple tracked observations")
    return matches[0] if matches else None


class SnapshotAdbTrackedDeviceLookup:
    """Single-observation lookup over freshly identified track-devices snapshots."""

    def __init__(self, snapshot_reader: AdbDevicesSnapshotReader) -> None:
        if not callable(getattr(snapshot_reader, "read", None)):
            raise TypeError("snapshot_reader must provide read()")
        self.snapshot_reader = snapshot_reader

    def find(
        self,
        server: AdbServerLifetime,
        selector: AdbTransportSelector,
    ) -> AdbTrackedTransportObservation | None:
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
