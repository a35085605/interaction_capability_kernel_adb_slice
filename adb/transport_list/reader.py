from __future__ import annotations

from typing import Protocol

from adb.adapters.aosp.track_devices import (
    SmartSocketAdbTransportListReader as _SmartSocketAdbTransportListReader,
)
from adb.transport_list.model import AdbTransportList
from networking import TcpAddress


class AdbTransportListReader(Protocol):
    """Read one complete current domain transport list."""

    def read(self, endpoint: TcpAddress) -> AdbTransportList:
        ...


class SmartSocketAdbTransportListReader:
    """Normalize one smart-socket transport list into the domain value."""

    def __init__(self) -> None:
        self._transport_list_reader = _SmartSocketAdbTransportListReader()

    def read(self, endpoint: TcpAddress) -> AdbTransportList:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        return AdbTransportList(
            transports=self._transport_list_reader.read(endpoint),
        )


__all__ = [
    "AdbTransportListReader",
    "SmartSocketAdbTransportListReader",
]
