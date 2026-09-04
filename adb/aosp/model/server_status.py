from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from numbers import Integral

from adb.errors import AdbProtocolError
from adb.aosp.protocol.protobuf import ProtoReader


def _require_string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    return value


def _require_optional_string(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, field_name=field_name)


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
        return raw


class AdbUsbBackend(IntEnum):
    """AOSP ``adb_host.proto.UsbBackend`` values."""

    UNKNOWN_USB = 0
    NATIVE = 1
    LIBUSB = 2


class AdbMdnsBackend(IntEnum):
    """AOSP ``adb_host.proto.MdnsBackend`` values."""

    UNKNOWN_MDNS = 0
    BONJOUR = 1
    OPENSCREEN = 2


@dataclass(frozen=True, slots=True)
class AdbServerStatus:
    """AOSP ``adb_host.proto.AdbServerStatus`` payload."""

    usb_backend: AdbUsbBackend | int = AdbUsbBackend.UNKNOWN_USB
    usb_backend_forced: bool = False
    mdns_backend: AdbMdnsBackend | int = AdbMdnsBackend.UNKNOWN_MDNS
    mdns_backend_forced: bool = False
    version: str = ""
    build: str = ""
    executable_absolute_path: str = ""
    log_absolute_path: str = ""
    os: str = ""
    trace_level: str | None = None
    burst_mode: bool | None = None
    mdns_enabled: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "usb_backend",
            _normalize_open_enum(
                self.usb_backend,
                AdbUsbBackend,
                field_name="ADB USB backend",
            ),
        )
        object.__setattr__(
            self,
            "mdns_backend",
            _normalize_open_enum(
                self.mdns_backend,
                AdbMdnsBackend,
                field_name="ADB mDNS backend",
            ),
        )
        for field_name in ("usb_backend_forced", "mdns_backend_forced"):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"ADB server {field_name} must be bool")
        for field_name in (
            "version",
            "build",
            "executable_absolute_path",
            "log_absolute_path",
            "os",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_string(getattr(self, field_name), field_name=f"ADB server {field_name}"),
            )
        object.__setattr__(
            self,
            "trace_level",
            _require_optional_string(self.trace_level, field_name="ADB server trace_level"),
        )
        for field_name in ("burst_mode", "mdns_enabled"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, bool):
                raise TypeError(f"ADB server {field_name} must be bool or None")




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


__all__ = ["AdbMdnsBackend", "AdbServerStatus", "AdbUsbBackend", "parse_server_status"]
