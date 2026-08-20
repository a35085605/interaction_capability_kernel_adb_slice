from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral
from typing import TypeAlias


def _normalize_required_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True, order=True)
class AdbDeviceSerial:
    """Native ADB device serial used for deterministic transport selection."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _normalize_required_text(self.value, field_name="ADB device serial"),
        )

    def __str__(self) -> str:
        return self.value


class AdbTransportId(int):
    """ADB-server-local native transport identity.

    Transport IDs are positive runtime identities allocated by one ADB server. They are
    integers on the native protocol, so the type intentionally subclasses ``int`` while
    preserving a distinct constructor for public APIs and selectors.
    """

    def __new__(cls, value: object) -> "AdbTransportId":
        if isinstance(value, bool) or not isinstance(value, Integral):
            raise TypeError("ADB transport id must be an integer")
        normalized = int(value)
        if normalized <= 0:
            raise ValueError("ADB transport id must be greater than zero")
        return int.__new__(cls, normalized)

    @property
    def value(self) -> int:
        return int(self)


@dataclass(frozen=True, slots=True)
class AdbTransportBySerial:
    """Select the transport directly by its native ADB serial.

    This selector is passed through to native ADB serial-selection mechanisms. It does not
    require an inventory snapshot and does not imply conversion to ``AdbTransportById``.
    """

    serial: AdbDeviceSerial

    def __post_init__(self) -> None:
        if not isinstance(self.serial, AdbDeviceSerial):
            raise TypeError("serial must be AdbDeviceSerial")


@dataclass(frozen=True, slots=True)
class AdbTransportById:
    """Select one exact ADB-server-local runtime transport identity."""

    transport_id: AdbTransportId

    def __post_init__(self) -> None:
        if not isinstance(self.transport_id, AdbTransportId):
            raise TypeError("transport_id must be AdbTransportId")


AdbTransportSelector: TypeAlias = AdbTransportBySerial | AdbTransportById


__all__ = [
    "AdbDeviceSerial",
    "AdbTransportById",
    "AdbTransportBySerial",
    "AdbTransportId",
    "AdbTransportSelector",
]
