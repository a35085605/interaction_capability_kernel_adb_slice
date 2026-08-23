from __future__ import annotations

from adb.transport.inventory.model import (
    AdbConnectionState,
    AdbConnectionType,
    AdbDevicesSnapshot,
    AdbTrackedDevice,
)
from adb.errors import AdbProtocolError
from adb.server.status.model import AdbMdnsBackend, AdbServerStatus, AdbUsbBackend
from adb.transport.selection import AdbTransportId


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
_SERVER_STRING_FIELDS = {
    5: "version",
    6: "build",
    7: "executable_absolute_path",
    8: "log_absolute_path",
    9: "os",
    10: "trace_level",
}


class ProtoReader:
    """Minimal protobuf wire reader for AOSP ``adb_host.proto`` payloads."""

    def __init__(self, payload: bytes) -> None:
        if not isinstance(payload, bytes):
            raise TypeError("protobuf payload must be bytes")
        self.payload = payload
        self.offset = 0

    @property
    def done(self) -> bool:
        return self.offset == len(self.payload)

    def read_varint(self) -> int:
        result = 0
        for byte_index in range(10):
            if self.offset >= len(self.payload):
                raise AdbProtocolError("truncated protobuf varint")
            byte = self.payload[self.offset]
            self.offset += 1
            if byte_index == 9 and byte > 1:
                raise AdbProtocolError("protobuf varint exceeds 64 bits")
            result |= (byte & 0x7F) << (7 * byte_index)
            if byte < 0x80:
                return result
        raise AdbProtocolError("protobuf varint exceeds 64 bits")

    def read_key(self) -> tuple[int, int]:
        key = self.read_varint()
        field_number = key >> 3
        wire_type = key & 0x07
        if field_number == 0:
            raise AdbProtocolError("protobuf field number cannot be zero")
        return field_number, wire_type

    def read_bytes(self) -> bytes:
        size = self.read_varint()
        end = self.offset + size
        if end > len(self.payload):
            raise AdbProtocolError("truncated protobuf length-delimited field")
        value = self.payload[self.offset:end]
        self.offset = end
        return value

    def skip(self, wire_type: int) -> None:
        if wire_type == 0:
            self.read_varint()
            return
        if wire_type == 1:
            self._skip_fixed(8)
            return
        if wire_type == 2:
            self.read_bytes()
            return
        if wire_type == 5:
            self._skip_fixed(4)
            return
        raise AdbProtocolError(f"unsupported protobuf wire type {wire_type}")

    def _skip_fixed(self, size: int) -> None:
        end = self.offset + size
        if end > len(self.payload):
            raise AdbProtocolError("truncated protobuf fixed-width field")
        self.offset = end


def _decode_utf8(raw: bytes, *, field_name: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdbProtocolError(f"ADB protobuf {field_name} is not valid UTF-8") from exc


def _decode_int64(raw: int) -> int:
    if raw >= (1 << 63):
        return raw - (1 << 64)
    return raw


def _decode_device(payload: bytes) -> AdbTrackedDevice:
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
                values["state"] = AdbConnectionState(raw_state)
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
                values["connection_type"] = AdbConnectionType(raw_type)
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
            raw_transport_id = _decode_int64(reader.read_varint())
            values["transport_id"] = (
                0 if raw_transport_id == 0 else AdbTransportId(raw_transport_id)
            )
            continue

        reader.skip(wire_type)

    return AdbTrackedDevice(**values)


def parse_devices_snapshot(payload: bytes) -> AdbDevicesSnapshot:
    reader = ProtoReader(payload)
    devices: list[AdbTrackedDevice] = []

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

    return AdbDevicesSnapshot(tuple(devices))


def parse_server_status(payload: bytes) -> AdbServerStatus:
    reader = ProtoReader(payload)
    values: dict[str, object] = {}

    while not reader.done:
        field_number, wire_type = reader.read_key()

        if field_number in (1, 3):
            if wire_type != 0:
                raise AdbProtocolError(
                    f"ADB server enum field {field_number} has wire type {wire_type}, expected 0"
                )
            raw = reader.read_varint()
            enum_type = AdbUsbBackend if field_number == 1 else AdbMdnsBackend
            name = "usb_backend" if field_number == 1 else "mdns_backend"
            try:
                values[name] = enum_type(raw)
            except ValueError:
                values[name] = raw
            continue

        if field_number in (2, 4, 11, 12):
            if wire_type != 0:
                raise AdbProtocolError(
                    f"ADB server bool field {field_number} has wire type {wire_type}, expected 0"
                )
            name = {
                2: "usb_backend_forced",
                4: "mdns_backend_forced",
                11: "burst_mode",
                12: "mdns_enabled",
            }[field_number]
            values[name] = bool(reader.read_varint())
            continue

        string_field = _SERVER_STRING_FIELDS.get(field_number)
        if string_field is not None:
            if wire_type != 2:
                raise AdbProtocolError(
                    f"ADB server field {field_number} has wire type {wire_type}, expected 2"
                )
            values[string_field] = _decode_utf8(
                reader.read_bytes(), field_name=string_field
            )
            continue

        reader.skip(wire_type)

    return AdbServerStatus(**values)


__all__ = ["parse_devices_snapshot", "parse_server_status"]
