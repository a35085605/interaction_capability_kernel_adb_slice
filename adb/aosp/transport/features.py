from __future__ import annotations

from dataclasses import dataclass, field

from adb.aosp.errors import AdbProtocolError


def _normalize_feature(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("ADB transport feature must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("ADB transport feature cannot be empty")
    if "," in normalized:
        raise ValueError("ADB transport feature cannot contain a comma")
    return normalized


@dataclass(frozen=True, slots=True)
class AdbTransportFeatures:
    """Native features advertised by one selected ADB transport."""

    features: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.features, frozenset):
            raise TypeError("ADB transport features must be a frozenset")
        normalized = frozenset(_normalize_feature(feature) for feature in self.features)
        object.__setattr__(self, "features", normalized)

    def __contains__(self, feature: object) -> bool:
        return feature in self.features


def parse_transport_features(payload: bytes) -> AdbTransportFeatures:
    """Decode one comma-separated ADB transport feature response."""

    if not isinstance(payload, bytes):
        raise TypeError("ADB transport feature payload must be bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdbProtocolError("ADB feature list is not valid UTF-8") from exc
    return AdbTransportFeatures(frozenset(part for part in text.split(",") if part))


__all__ = ["AdbTransportFeatures", "parse_transport_features"]
