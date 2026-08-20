from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from native_attempt import NativeAttemptResult


def _normalize_required_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class AdbWirelessPair:
    """Request one pairing attempt for an explicit wireless-debugging pairing endpoint."""

    address: str
    pairing_code: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "address",
            _normalize_required_text(self.address, field_name="ADB pairing address"),
        )
        object.__setattr__(
            self,
            "pairing_code",
            _normalize_required_text(self.pairing_code, field_name="ADB pairing code"),
        )


class AdbWirelessPairer(Protocol):
    def pair(self, operation: AdbWirelessPair) -> NativeAttemptResult: ...


__all__ = ["AdbWirelessPair", "AdbWirelessPairer"]
