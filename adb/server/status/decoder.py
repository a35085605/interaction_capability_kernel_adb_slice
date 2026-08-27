from __future__ import annotations

from adb._internal.protobuf_wire import ProtoReader
from adb.errors import AdbProtocolError
from adb.server.status.model import AdbMdnsBackend, AdbServerStatus, AdbUsbBackend


_SERVER_STRING_FIELDS = {
    5: "version",
    6: "build",
    7: "executable_absolute_path",
    8: "log_absolute_path",
    9: "os",
    10: "trace_level",
}


def _decode_utf8(raw: bytes, *, field_name: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdbProtocolError(f"ADB protobuf {field_name} is not valid UTF-8") from exc


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


__all__ = ["parse_server_status"]
