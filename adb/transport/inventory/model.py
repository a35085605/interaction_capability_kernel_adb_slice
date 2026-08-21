from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from numbers import Integral
from typing import Literal

from adb.transport.selection import AdbTransportId


class AdbConnectionState(IntEnum):
    """AOSP ``adb_host.proto.ConnectionState`` values."""

    ANY = 0
    CONNECTING = 1
    AUTHORIZING = 2
    UNAUTHORIZED = 3
    NOPERMISSION = 4
    DETACHED = 5
    OFFLINE = 6
    BOOTLOADER = 7
    DEVICE = 8
    HOST = 9
    RECOVERY = 10
    SIDELOAD = 11
    RESCUE = 12


class AdbConnectionType(IntEnum):
    """AOSP ``adb_host.proto.ConnectionType`` values."""

    UNKNOWN = 0
    USB = 1
    SOCKET = 2


def _require_string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    return value


def _require_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError(f"{field_name} must be an integer")
    return int(value)


def _normalize_open_enum(
    value: object,
    enum_type: type[IntEnum],
    *,
    field_name: str,
) -> IntEnum | int:
    raw = _require_int(value, field_name=field_name)
    try:
        return enum_type(raw)
    except ValueError:
        # Proto3 enums are open: preserve future AOSP values numerically instead
        # of inventing an UNKNOWN interpretation or rejecting the whole snapshot.
        return raw


@dataclass(frozen=True, slots=True)
class AdbTrackedDevice:
    """One observed row from AOSP ``adb_host.proto.Device``.

    This wire-aligned value describes one server-tracked ADB transport in an
    inventory snapshot. It is not an independently identified device entity and
    does not own a separate lifecycle or command surface. ``transport_id`` is the
    native server-local transport identity when non-zero; zero means that native
    identity is unavailable in the observed row.

    Known enum numbers are exposed as the matching ``IntEnum`` member. Unknown
    future proto3 enum numbers are preserved as raw integers.
    """

    serial: str = ""
    state: AdbConnectionState | int = AdbConnectionState.ANY
    bus_address: str = ""
    product: str = ""
    model: str = ""
    device: str = ""
    connection_type: AdbConnectionType | int = AdbConnectionType.UNKNOWN
    negotiated_speed: int = 0
    max_speed: int = 0
    transport_id: AdbTransportId | Literal[0] = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "state",
            _normalize_open_enum(
                self.state,
                AdbConnectionState,
                field_name="ADB connection state",
            ),
        )
        object.__setattr__(
            self,
            "connection_type",
            _normalize_open_enum(
                self.connection_type,
                AdbConnectionType,
                field_name="ADB connection type",
            ),
        )

        for field_name in ("serial", "bus_address", "product", "model", "device"):
            object.__setattr__(
                self,
                field_name,
                _require_string(
                    getattr(self, field_name),
                    field_name=f"ADB device {field_name}",
                ),
            )

        for field_name in ("negotiated_speed", "max_speed"):
            object.__setattr__(
                self,
                field_name,
                _require_int(
                    getattr(self, field_name),
                    field_name=f"ADB device {field_name}",
                ),
            )

        transport_id = self.transport_id
        if isinstance(transport_id, AdbTransportId):
            pass
        elif isinstance(transport_id, bool) or not isinstance(transport_id, Integral):
            raise TypeError(
                "ADB device transport_id must be AdbTransportId or integer zero"
            )
        else:
            raw_transport_id = int(transport_id)
            if raw_transport_id < 0:
                raise ValueError("ADB device transport_id cannot be negative")
            object.__setattr__(
                self,
                "transport_id",
                0 if raw_transport_id == 0 else AdbTransportId(raw_transport_id),
            )


@dataclass(frozen=True, slots=True)
class AdbDevicesSnapshot:
    """Complete AOSP ``adb_host.proto.Devices`` transport-inventory snapshot."""

    devices: tuple[AdbTrackedDevice, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.devices, tuple):
            raise TypeError("ADB devices must be a tuple")
        for index, device in enumerate(self.devices):
            if not isinstance(device, AdbTrackedDevice):
                raise TypeError(f"ADB devices[{index}] must be AdbTrackedDevice")


__all__ = [
    "AdbConnectionState",
    "AdbConnectionType",
    "AdbDevicesSnapshot",
    "AdbTrackedDevice",
]
