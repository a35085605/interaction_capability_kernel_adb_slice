from __future__ import annotations

from typing import Protocol

from adb.errors import AdbTransportAmbiguousError
from networking import TcpAddress
from adb.transport.model import AdbTransport
from adb.transport_list.model import AdbTransportList
from adb.transport.selection import (
    AdbTransportById,
    AdbTransportBySerial,
    AdbTransportSelector,
)


class AdbTransportLookup(Protocol):
    """Find one transport in a freshly read transport list."""

    def find(
        self,
        endpoint: TcpAddress,
        selector: AdbTransportSelector,
    ) -> AdbTransport | None:
        ...


def find_transport(
    transport_list: AdbTransportList,
    selector: AdbTransportSelector,
) -> AdbTransport | None:
    """Select at most one transport from an already-observed transport list.

    Raise ``AdbTransportAmbiguousError`` when the selector matches multiple entries.
    """

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
        raise AdbTransportAmbiguousError(
            "ADB transport selector matched more than one transport"
        )
    return matches[0] if matches else None


__all__ = [
    "AdbTransportLookup",
    "find_transport",
]
