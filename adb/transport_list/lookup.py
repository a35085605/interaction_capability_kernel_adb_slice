from __future__ import annotations

from typing import Protocol

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.transport.model import AdbTransport
from adb.transport_list.model import AdbTransportList
from adb.transport_list.reader import AdbTransportListReader
from adb.transport.selection import (
    AdbTransportById,
    AdbTransportBySerial,
    AdbTransportSelector,
)


class AdbTransportLookup(Protocol):
    """Find one transport in a freshly read transport list."""

    def find(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
    ) -> AdbTransport | None:
        ...


def find_transport(
    transport_list: AdbTransportList,
    selector: AdbTransportSelector,
) -> AdbTransport | None:
    """Select a transport from a transport list."""

    if not isinstance(transport_list, AdbTransportList):
        raise TypeError("transport_list must be AdbTransportList")
    if isinstance(selector, AdbTransportBySerial):
        matches = [
            transport
            for transport in transport_list
            if transport.matches_serial(selector.serial)
        ]
    elif isinstance(selector, AdbTransportById):
        matches = [
            transport
            for transport in transport_list
            if transport.transport_id == selector.transport_id
        ]
    else:
        raise TypeError("selector must be AdbTransportBySerial or AdbTransportById")

    if len(matches) > 1:
        raise ValueError("ADB transport selector matched multiple transports")
    return matches[0] if matches else None


class ReadingAdbTransportLookup:
    """Single-transport lookup backed by freshly read transport lists."""

    def __init__(self, transport_list_reader: AdbTransportListReader) -> None:
        if not callable(getattr(transport_list_reader, "read", None)):
            raise TypeError("transport_list_reader must provide read()")
        self.transport_list_reader = transport_list_reader

    def find(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
    ) -> AdbTransport | None:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        transport_list = self.transport_list_reader.read(endpoint)
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport-list reader must return AdbTransportList")
        return find_transport(transport_list, selector)


__all__ = [
    "AdbTransportLookup",
    "ReadingAdbTransportLookup",
    "find_transport",
]
