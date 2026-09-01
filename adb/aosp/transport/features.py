from __future__ import annotations

from adb.aosp.errors import AdbProtocolError
from adb.transport.features import AdbTransportFeatures


def parse_transport_features(payload: bytes) -> AdbTransportFeatures:
    """Decode one native comma-separated ADB transport feature response."""

    if not isinstance(payload, bytes):
        raise TypeError("ADB transport feature payload must be bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdbProtocolError("ADB feature list is not valid UTF-8") from exc
    return AdbTransportFeatures(frozenset(part for part in text.split(",") if part))


__all__ = ["AdbTransportFeatures", "parse_transport_features"]
