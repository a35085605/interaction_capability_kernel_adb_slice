from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from adb.server.endpoint import AdbServerEndpoint
from adb.transport.selection import AdbDeviceSerial

if TYPE_CHECKING:
    from adb.transport.inventory.model import AdbConnectionType


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

    ``serial`` remains the persistent selection and inventory-resolution identity. ``address``
    is only the explicit endpoint supplied to ``adb connect`` when readiness ensuring observes the
    configured serial as absent; the address need not equal the serial later reported by ADB.
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
    """ADB-domain configuration for one endpoint-bound transport.

    The nested transport configuration makes USB and TCP establishment semantics explicit while
    keeping ``serial`` as the stable native selection key. Runtime ``transport_id`` values remain
    fresh inventory facts rather than configured identity or implicit ensure-operation state.
    """

    endpoint: AdbServerEndpoint
    transport: AdbTransportConfiguration

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if not isinstance(
            self.transport,
            (AdbUsbTransportConfiguration, AdbTcpTransportConfiguration),
        ):
            raise TypeError("transport must be an ADB transport configuration")

    @property
    def serial(self) -> AdbDeviceSerial:
        """Persistent selection and inventory-resolution identity for this transport."""

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

        from adb.transport.inventory.model import AdbConnectionType

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
