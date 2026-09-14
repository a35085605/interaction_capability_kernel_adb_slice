from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.transport.address import AdbConnectAddress
from adb.transport.identity import AdbDeviceSerial


@dataclass(frozen=True, slots=True)
class AdbUsbTransportSpec:
    """Specify one serial-selected USB ADB transport."""

    serial: AdbDeviceSerial

    def __post_init__(self) -> None:
        if not isinstance(self.serial, AdbDeviceSerial):
            raise TypeError("serial must be AdbDeviceSerial")


@dataclass(frozen=True, slots=True)
class AdbTcpTransportSpec:
    """Specify one serial-selected TCP ADB transport with a connect target used to establish
    absent serials.
    """

    serial: AdbDeviceSerial
    connect_address: AdbConnectAddress

    def __post_init__(self) -> None:
        if not isinstance(self.serial, AdbDeviceSerial):
            raise TypeError("serial must be AdbDeviceSerial")
        if not isinstance(self.connect_address, AdbConnectAddress):
            raise TypeError("connect_address must be AdbConnectAddress")


AdbTransportSpec: TypeAlias = AdbUsbTransportSpec | AdbTcpTransportSpec


__all__ = [
    "AdbTcpTransportSpec",
    "AdbTransportSpec",
    "AdbUsbTransportSpec",
]
