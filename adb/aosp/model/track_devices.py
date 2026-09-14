from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from adb.aosp.model._validation import (
    normalize_open_enum,
    require_int,
    require_string,
)
from adb.aosp.protocol.protobuf import ProtoReader, decode_utf8, require_wire_type


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


@dataclass(frozen=True, slots=True)
class Device:
    """Decoded AOSP ``adb.proto.Device`` record with transport and open-enum evidence."""

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
            normalize_open_enum(
                self.state,
                ConnectionState,
                field_name="ADB connection state",
            ),
        )
        object.__setattr__(
            self,
            "connection_type",
            normalize_open_enum(
                self.connection_type,
                ConnectionType,
                field_name="ADB connection type",
            ),
        )

        for field_name in ("serial", "bus_address", "product", "model", "device"):
            object.__setattr__(
                self,
                field_name,
                require_string(
                    getattr(self, field_name),
                    field_name=f"ADB device {field_name}",
                ),
            )

        for field_name in ("negotiated_speed", "max_speed"):
            object.__setattr__(
                self,
                field_name,
                require_int(
                    getattr(self, field_name),
                    field_name=f"ADB device {field_name}",
                ),
            )

        object.__setattr__(
            self,
            "transport_id",
            require_int(self.transport_id, field_name="ADB device transport_id"),
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


_DEVICE_STRING_FIELDS = {
    1: "serial",
    3: "bus_address",
    4: "product",
    5: "model",
    6: "device",
}
_DEVICE_INT64_FIELDS = {
    8: "negotiated_speed",
    9: "max_speed",
}


def _decode_int64(raw: int) -> int:
    if raw >= (1 << 63):
        return raw - (1 << 64)
    return raw


def _decode_device(payload: bytes) -> Device:
    reader = ProtoReader(payload)
    values: dict[str, object] = {}

    while not reader.done:
        field_number, wire_type = reader.read_key()

        string_field = _DEVICE_STRING_FIELDS.get(field_number)
        if string_field is not None:
            require_wire_type(
                wire_type,
                2,
                context=f"ADB Device field {field_number}",
            )
            values[string_field] = decode_utf8(
                reader.read_bytes(), field_name=string_field
            )
            continue

        if field_number == 2:
            require_wire_type(wire_type, 0, context="ADB Device state")
            raw_state = reader.read_varint()
            values["state"] = normalize_open_enum(
                raw_state,
                ConnectionState,
                field_name="ADB Device state",
            )
            continue

        if field_number == 7:
            require_wire_type(wire_type, 0, context="ADB Device connection_type")
            raw_type = reader.read_varint()
            values["connection_type"] = normalize_open_enum(
                raw_type,
                ConnectionType,
                field_name="ADB Device connection_type",
            )
            continue

        int64_field = _DEVICE_INT64_FIELDS.get(field_number)
        if int64_field is not None:
            require_wire_type(
                wire_type,
                0,
                context=f"ADB Device field {field_number}",
            )
            values[int64_field] = _decode_int64(reader.read_varint())
            continue

        if field_number == 10:
            require_wire_type(wire_type, 0, context="ADB Device transport_id")
            values["transport_id"] = _decode_int64(reader.read_varint())
            continue

        reader.skip(wire_type)

    return Device(**values)


def parse_devices(payload: bytes) -> Devices:
    reader = ProtoReader(payload)
    devices: list[Device] = []

    while not reader.done:
        field_number, wire_type = reader.read_key()
        if field_number == 1:
            require_wire_type(wire_type, 2, context="ADB Devices.device")
            devices.append(_decode_device(reader.read_bytes()))
            continue
        reader.skip(wire_type)

    return Devices(tuple(devices))


__all__ = ["ConnectionState", "ConnectionType", "Device", "Devices", "parse_devices"]
