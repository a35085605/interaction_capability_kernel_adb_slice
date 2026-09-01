from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.transport.identity import AdbDeviceSerial, AdbTransportId


@dataclass(frozen=True, slots=True)
class AdbTransportBySerial:
    """Select an ADB transport by serial."""

    serial: AdbDeviceSerial

    def __post_init__(self) -> None:
        if not isinstance(self.serial, AdbDeviceSerial):
            raise TypeError("serial must be AdbDeviceSerial")


@dataclass(frozen=True, slots=True)
class AdbTransportById:
    """Select a transport by its server-local transport ID."""

    transport_id: AdbTransportId

    def __post_init__(self) -> None:
        if not isinstance(self.transport_id, AdbTransportId):
            raise TypeError("transport_id must be AdbTransportId")


AdbTransportSelector: TypeAlias = AdbTransportBySerial | AdbTransportById


__all__ = [
    "AdbDeviceSerial",
    "AdbTransportById",
    "AdbTransportBySerial",
    "AdbTransportId",
    "AdbTransportSelector",
]
