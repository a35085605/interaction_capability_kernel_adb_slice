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
class AdbServerEndpoint:
    """TCP address of one host-side ADB smart-socket server."""

    host: str = "localhost"
    port: int = 5037

    def __post_init__(self) -> None:
        host = _normalize_required_text(self.host, field_name="ADB server endpoint host")
        if isinstance(self.port, bool) or not isinstance(self.port, Integral):
            raise TypeError("ADB server endpoint port must be an integer")
        port = int(self.port)
        if not 1 <= port <= 65535:
            raise ValueError("ADB server endpoint port must be between 1 and 65535")
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "port", port)


__all__ = ["AdbServerEndpoint"]
