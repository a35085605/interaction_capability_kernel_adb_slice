from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from numbers import Integral


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


__all__ = ["AdbMdnsBackend", "AdbServerStatus", "AdbUsbBackend"]
