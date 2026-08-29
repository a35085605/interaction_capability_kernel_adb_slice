from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from adb.aosp.transport.address import AdbConnectAddress
from adb.transport.identity import AdbDeviceSerial


class AdbTransportType(str, Enum):
    """Domain transport kinds supported by configured ADB transports."""

    USB = "usb"
    TCP = "tcp"


@dataclass(frozen=True, slots=True)
class AdbUsbTransportConfiguration:
    """Configuration for one serial-selected USB ADB transport."""

    serial: AdbDeviceSerial

    def __post_init__(self) -> None:
        if not isinstance(self.serial, AdbDeviceSerial):
            raise TypeError("serial must be AdbDeviceSerial")


@dataclass(frozen=True, slots=True)
class AdbTcpTransportConfiguration:
    """Configuration for one serial-selected TCP ADB transport.

    ``serial`` identifies the transport for selection and tracking; ``connect_address`` is used
    only for ``adb connect`` when the serial is absent. The reported serial may differ from the
    connect address.
    """

    serial: AdbDeviceSerial
    connect_address: AdbConnectAddress

    def __post_init__(self) -> None:
        if not isinstance(self.serial, AdbDeviceSerial):
            raise TypeError("serial must be AdbDeviceSerial")
        if not isinstance(self.connect_address, AdbConnectAddress):
            raise TypeError("connect_address must be AdbConnectAddress")


AdbTransportConfiguration: TypeAlias = (
    AdbUsbTransportConfiguration | AdbTcpTransportConfiguration
)


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransport:
    """Server-independent configuration for one ADB transport.

    The serial is the stable selection identity; transport IDs are server-local runtime facts.
    """

    transport: AdbTransportConfiguration

    def __post_init__(self) -> None:
        if not isinstance(
            self.transport,
            (AdbUsbTransportConfiguration, AdbTcpTransportConfiguration),
        ):
            raise TypeError("transport must be an ADB transport configuration")

    @property
    def serial(self) -> AdbDeviceSerial:
        """Persistent selection and tracking-resolution identity for this transport."""

        return self.transport.serial

    @property
    def connect_address(self) -> AdbConnectAddress | None:
        """Explicit native connect address when this configured transport uses TCP."""

        if isinstance(self.transport, AdbTcpTransportConfiguration):
            return self.transport.connect_address
        return None

    @property
    def type(self) -> AdbTransportType:
        """Domain transport kind for this configuration."""

        if isinstance(self.transport, AdbUsbTransportConfiguration):
            return AdbTransportType.USB
        return AdbTransportType.TCP


__all__ = [
    "AdbConfiguredTransport",
    "AdbTcpTransportConfiguration",
    "AdbTransportConfiguration",
    "AdbTransportType",
    "AdbUsbTransportConfiguration",
]
