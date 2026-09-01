from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from numbers import Integral

from adb.aosp.errors import AdbProtocolError
from adb.aosp.protocol.protobuf import ProtoReader


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


def _decode_utf8(raw: bytes, *, field_name: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdbProtocolError(f"ADB protobuf {field_name} is not valid UTF-8") from exc


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
            if wire_type != 2:
                raise AdbProtocolError(
                    f"ADB Device field {field_number} has wire type {wire_type}, expected 2"
                )
            values[string_field] = _decode_utf8(
                reader.read_bytes(), field_name=string_field
            )
            continue

        if field_number == 2:
            if wire_type != 0:
                raise AdbProtocolError(
                    f"ADB Device state has wire type {wire_type}, expected 0"
                )
            raw_state = reader.read_varint()
            try:
                values["state"] = ConnectionState(raw_state)
            except ValueError:
                values["state"] = raw_state
            continue

        if field_number == 7:
            if wire_type != 0:
                raise AdbProtocolError(
                    "ADB Device connection_type has wire type "
                    f"{wire_type}, expected 0"
                )
            raw_type = reader.read_varint()
            try:
                values["connection_type"] = ConnectionType(raw_type)
            except ValueError:
                values["connection_type"] = raw_type
            continue

        int64_field = _DEVICE_INT64_FIELDS.get(field_number)
        if int64_field is not None:
            if wire_type != 0:
                raise AdbProtocolError(
                    f"ADB Device field {field_number} has wire type {wire_type}, expected 0"
                )
            values[int64_field] = _decode_int64(reader.read_varint())
            continue

        if field_number == 10:
            if wire_type != 0:
                raise AdbProtocolError(
                    f"ADB Device transport_id has wire type {wire_type}, expected 0"
                )
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
            if wire_type != 2:
                raise AdbProtocolError(
                    f"ADB Devices.device has wire type {wire_type}, expected 2"
                )
            devices.append(_decode_device(reader.read_bytes()))
            continue
        reader.skip(wire_type)

    return Devices(tuple(devices))


__all__ = ["ConnectionState", "ConnectionType", "Device", "Devices", "parse_devices"]
