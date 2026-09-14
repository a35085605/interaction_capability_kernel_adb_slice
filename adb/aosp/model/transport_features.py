from __future__ import annotations

from adb.errors import AdbProtocolError


def parse_transport_features(payload: bytes) -> frozenset[str]:
    """Decode one AOSP ADB transport-feature payload.

    The wire payload is UTF-8 text containing comma-separated feature names. This
    decoder intentionally preserves each non-empty member exactly as advertised;
    domain-level normalization and validation belong to the adapter/domain layer.
    """

    if not isinstance(payload, bytes):
        raise TypeError("ADB transport feature payload must be bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdbProtocolError("ADB feature list is not valid UTF-8") from exc
    return frozenset(part for part in text.split(",") if part)


__all__ = ["parse_transport_features"]
