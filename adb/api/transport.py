from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from adb.transport.address import AdbConnectAddress
from adb.transport.configuration import (
    AdbConfiguredTransport,
    AdbTcpTransportConfiguration,
    AdbUsbTransportConfiguration,
)
from adb.transport.lifecycle.supervision.policy import (
    AdbConfiguredTransportSupervisionPolicy,
)
from adb.transport.identity import AdbDeviceSerial


class AdbConfiguredTransportType(str, Enum):
    """Transport kinds accepted by the public declarative registration API."""

    USB = "usb"
    TCP = "tcp"


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransportRegistration:
    """Declarative public registration for one runtime-managed ADB transport."""

    serial: AdbDeviceSerial
    type: AdbConfiguredTransportType
    connect_address: AdbConnectAddress | None = None
    policy: AdbConfiguredTransportSupervisionPolicy = field(
        default_factory=AdbConfiguredTransportSupervisionPolicy
    )

    def __post_init__(self) -> None:
        if not isinstance(self.serial, AdbDeviceSerial):
            raise TypeError("serial must be AdbDeviceSerial")
        if not isinstance(self.type, AdbConfiguredTransportType):
            raise TypeError("type must be AdbConfiguredTransportType")
        if self.connect_address is not None and not isinstance(
            self.connect_address, AdbConnectAddress
        ):
            raise TypeError("connect_address must be AdbConnectAddress or None")
        if not isinstance(self.policy, AdbConfiguredTransportSupervisionPolicy):
            raise TypeError("policy must be AdbConfiguredTransportSupervisionPolicy")

        if self.type is AdbConfiguredTransportType.USB:
            if self.connect_address is not None:
                raise ValueError("USB configured transport must not have connect_address")
            if self.policy.tcp_recovery_ensure_policy is not None:
                raise ValueError("USB configured transport cannot enable TCP recovery")
            return

        if self.connect_address is None:
            raise ValueError("TCP configured transport requires connect_address")


def _configured_transport_from_registration(
    registration: AdbConfiguredTransportRegistration,
) -> AdbConfiguredTransport:
    """Translate the public registration value at the API boundary."""

    if not isinstance(registration, AdbConfiguredTransportRegistration):
        raise TypeError("registration must be AdbConfiguredTransportRegistration")

    if registration.type is AdbConfiguredTransportType.USB:
        transport = AdbUsbTransportConfiguration(registration.serial)
    else:
        connect_address = registration.connect_address
        if connect_address is None:
            raise RuntimeError("validated TCP registration lost its connect_address")
        transport = AdbTcpTransportConfiguration(
            serial=registration.serial,
            connect_address=connect_address,
        )
    return AdbConfiguredTransport(transport)


__all__ = [
    "AdbConfiguredTransportRegistration",
    "AdbConfiguredTransportType",
]
