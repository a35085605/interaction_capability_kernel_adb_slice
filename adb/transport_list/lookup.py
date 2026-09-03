from __future__ import annotations

from typing import Protocol

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.transport.model import AdbTransport
from adb.transport_list.model import AdbTransportList
from adb.transport_list.reader import AdbTransportListSnapshotReader
from adb.transport.selection import (
    AdbTransportById,
    AdbTransportBySerial,
    AdbTransportSelector,
)


class AdbTransportLookup(Protocol):
    """Find one transport in a fresh transport-list snapshot."""

    def find(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
    ) -> AdbTransport | None:
        ...


def find_transport(
    snapshot: AdbTransportList,
    selector: AdbTransportSelector,
) -> AdbTransport | None:
    """Select one domain transport from a transport-list snapshot."""

    if not isinstance(snapshot, AdbTransportList):
        raise TypeError("snapshot must be AdbTransportList")
    if isinstance(selector, AdbTransportBySerial):
        matches = [
            transport
            for transport in snapshot
            if transport.matches_serial(selector.serial)
        ]
    elif isinstance(selector, AdbTransportById):
        matches = [
            transport
            for transport in snapshot
            if transport.transport_id == selector.transport_id
        ]
    else:
        raise TypeError("selector must be AdbTransportBySerial or AdbTransportById")

    if len(matches) > 1:
        raise ValueError("ADB transport selector matched multiple transports")
    return matches[0] if matches else None


class SnapshotAdbTransportLookup:
    """Single-transport lookup over freshly identified transport-list snapshots."""

    def __init__(self, snapshot_reader: AdbTransportListSnapshotReader) -> None:
        if not callable(getattr(snapshot_reader, "read", None)):
            raise TypeError("snapshot_reader must provide read()")
        self.snapshot_reader = snapshot_reader

    def find(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
    ) -> AdbTransport | None:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        snapshot = self.snapshot_reader.read(endpoint)
        if not isinstance(snapshot, AdbTransportList):
            raise TypeError("snapshot reader must return AdbTransportList")
        return find_transport(snapshot, selector)


__all__ = [
    "AdbTransportLookup",
    "SnapshotAdbTransportLookup",
    "find_transport",
]
