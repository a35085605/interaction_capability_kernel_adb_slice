from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from numbers import Integral


class ConnectionState(IntEnum):
    """AOSP ``adb.proto.ConnectionState`` values from ``adb_host.proto``."""

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


class ConnectionType(IntEnum):
    """AOSP ``adb.proto.ConnectionType`` values from ``adb_host.proto``."""

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
        # of inventing an UNKNOWN interpretation or rejecting the whole payload.
        return raw


@dataclass(frozen=True, slots=True)
class Device:
    """One AOSP ``adb.proto.Device`` value observed from ``track-devices``.

    This is protocol evidence, not a stable domain device identity. ``transport_id`` preserves
    the raw signed protobuf ``int64``; domain validation happens only when a consumer interprets
    it as an ``AdbTransportId``. Unknown enum values are preserved as integers.
    """

    serial: str = ""
    state: ConnectionState | int = ConnectionState.ANY
    bus_address: str = ""
    product: str = ""
    model: str = ""
    device: str = ""
    connection_type: ConnectionType | int = ConnectionType.UNKNOWN
    negotiated_speed: int = 0
    max_speed: int = 0
    transport_id: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "state",
            _normalize_open_enum(
                self.state,
                ConnectionState,
                field_name="ADB connection state",
            ),
        )
        object.__setattr__(
            self,
            "connection_type",
            _normalize_open_enum(
                self.connection_type,
                ConnectionType,
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

        object.__setattr__(
            self,
            "transport_id",
            _require_int(self.transport_id, field_name="ADB device transport_id"),
        )


@dataclass(frozen=True, slots=True)
class Devices:
    """AOSP ``adb.proto.Devices`` payload from ``adb_host.proto``."""

    devices: tuple[Device, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.devices, tuple):
            raise TypeError("ADB devices must be a tuple")
        for index, device in enumerate(self.devices):
            if not isinstance(device, Device):
                raise TypeError(f"ADB devices[{index}] must be Device")


__all__ = ["ConnectionState", "ConnectionType", "Device", "Devices"]
