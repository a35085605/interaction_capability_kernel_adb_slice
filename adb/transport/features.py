from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from adb.server.endpoint import AdbServerEndpoint
from adb.transport.selection import AdbTransportSelector


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
    """Open ADB transport feature set advertised by one selected transport."""

    features: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.features, frozenset):
            raise TypeError("ADB transport features must be a frozenset")
        normalized = frozenset(_normalize_feature(feature) for feature in self.features)
        object.__setattr__(self, "features", normalized)

    def __contains__(self, feature: object) -> bool:
        return feature in self.features


class AdbTransportFeaturesReader(Protocol):
    """Read feature facts for one deterministically selected ADB transport."""

    def read(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
    ) -> AdbTransportFeatures:
        ...


__all__ = ["AdbTransportFeatures", "AdbTransportFeaturesReader"]
