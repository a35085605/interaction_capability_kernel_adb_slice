from __future__ import annotations

from typing import Protocol

from adb.adapters.aosp.track_devices import SmartSocketAdbTransportListReader
from adb.transport_list.model import AdbTransportList
from networking import TcpAddress


class AdbTransportListSnapshotReader(Protocol):
    """Read one complete current domain transport-list snapshot."""

    def read(self, endpoint: TcpAddress) -> AdbTransportList:
        ...


class SmartSocketAdbTransportListSnapshotReader:
    """Normalize one smart-socket transport list into a domain snapshot value."""

    def __init__(self) -> None:
        self._transport_list_reader = SmartSocketAdbTransportListReader()

    def read(self, endpoint: TcpAddress) -> AdbTransportList:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        return AdbTransportList(
            transports=self._transport_list_reader.read(endpoint),
        )


__all__ = [
    "AdbTransportListSnapshotReader",
    "SmartSocketAdbTransportListSnapshotReader",
]
