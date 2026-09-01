from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral


def _normalize_required_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True, order=True)
class AdbDeviceSerial:
    """ADB device serial used as a stable transport-selection identity."""

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
    """Positive ADB-server-local transport identity validated from raw AOSP signed ``int64``
    values.
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


__all__ = ["AdbDeviceSerial", "AdbTransportId"]
