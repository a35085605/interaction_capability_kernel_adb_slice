from __future__ import annotations

from adb.protocol.protobuf import ProtoReader
from adb.errors import AdbProtocolError
from adb.tracking.snapshot.model import (
    AdbConnectionState,
    AdbConnectionType,
    AdbDevicesRecord,
    AdbTrackedDevice,
)

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
            values["transport_id"] = _decode_int64(reader.read_varint())
            continue

        reader.skip(wire_type)

    return AdbTrackedDevice(**values)


def parse_devices_record(payload: bytes) -> AdbDevicesRecord:
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

    return AdbDevicesRecord(tuple(devices))


__all__ = ["parse_devices_record"]
