from __future__ import annotations

from typing import Protocol

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.tracking.observation import AdbTrackedTransportObservation
from adb.tracking.snapshot.identity import AdbTransportListSnapshot
from adb.tracking.snapshot.reader import AdbTransportListSnapshotReader
from adb.transport.selection import (
    AdbTransportById,
    AdbTransportBySerial,
    AdbTransportSelector,
)


class AdbTrackedTransportLookup(Protocol):
    """Find one tracked transport observation in a fresh transport-list snapshot."""

    def find(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
    ) -> AdbTrackedTransportObservation | None:
        ...


def find_tracked_transport(
    snapshot: AdbTransportListSnapshot,
    selector: AdbTransportSelector,
) -> AdbTrackedTransportObservation | None:
    """Select one domain transport observation from a transport-list snapshot."""

    if not isinstance(snapshot, AdbTransportListSnapshot):
        raise TypeError("snapshot must be AdbTransportListSnapshot")
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


class SnapshotAdbTrackedTransportLookup:
    """Single-observation lookup over freshly identified transport-list snapshots."""

    def __init__(self, snapshot_reader: AdbTransportListSnapshotReader) -> None:
        if not callable(getattr(snapshot_reader, "read", None)):
            raise TypeError("snapshot_reader must provide read()")
        self.snapshot_reader = snapshot_reader

    def find(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
    ) -> AdbTrackedTransportObservation | None:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        snapshot = self.snapshot_reader.read(endpoint)
        if not isinstance(snapshot, AdbTransportListSnapshot):
            raise TypeError("snapshot reader must return AdbTransportListSnapshot")
        return find_tracked_transport(snapshot, selector)


__all__ = [
    "AdbTrackedTransportLookup",
    "SnapshotAdbTrackedTransportLookup",
    "find_tracked_transport",
]
