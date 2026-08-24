from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from adb.transport.selection import AdbDeviceSerial

if TYPE_CHECKING:
    from adb.tracking.model import AdbConnectionType


def _normalize_required_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True, order=True)
class AdbTcpAddress:
    """Explicit TCP address accepted by ADB connect/disconnect commands."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _normalize_required_text(self.value, field_name="ADB TCP address"),
        )


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

    ``serial`` identifies the transport for selection and tracking; ``address`` is used only
    for ``adb connect`` when the serial is absent. The reported serial may differ from the
    connect address.
    """

    serial: AdbDeviceSerial
    address: AdbTcpAddress

    def __post_init__(self) -> None:
        if not isinstance(self.serial, AdbDeviceSerial):
            raise TypeError("serial must be AdbDeviceSerial")
        if not isinstance(self.address, AdbTcpAddress):
            raise TypeError("address must be AdbTcpAddress")


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
    def connect_address(self) -> AdbTcpAddress | None:
        """Explicit TCP connect address when this configured transport uses TCP."""

        if isinstance(self.transport, AdbTcpTransportConfiguration):
            return self.transport.address
        return None

    @property
    def expected_connection_type(self) -> AdbConnectionType:
        """Observed ADB connection type required by this configured transport."""

        from adb.tracking.model import AdbConnectionType

        if isinstance(self.transport, AdbUsbTransportConfiguration):
            return AdbConnectionType.USB
        return AdbConnectionType.SOCKET


__all__ = [
    "AdbConfiguredTransport",
    "AdbTcpAddress",
    "AdbTcpTransportConfiguration",
    "AdbTransportConfiguration",
    "AdbUsbTransportConfiguration",
]
