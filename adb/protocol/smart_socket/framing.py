from __future__ import annotations

from adb.errors import AdbProtocolError


_HEX_DIGITS = frozenset(b"0123456789abcdefABCDEF")


def encode_service(service: str) -> bytes:
    """Encode one ADB smart-socket service request with its 4-hex length prefix."""

    if not isinstance(service, str):
        raise TypeError("ADB service must be a string")
    encoded = service.encode("utf-8")
    if not encoded:
        raise ValueError("ADB service cannot be empty")
    if len(encoded) > 0xFFFF:
        raise ValueError("ADB service name is too long")
    return f"{len(encoded):04x}".encode("ascii") + encoded


def parse_hex_length(raw: bytes, *, context: str) -> int:
    """Parse one ADB 4-hex smart-socket length prefix."""

    if len(raw) != 4 or any(byte not in _HEX_DIGITS for byte in raw):
        raise AdbProtocolError(f"invalid {context} length prefix: {raw!r}")
    return int(raw, 16)


__all__ = ["encode_service", "parse_hex_length"]
