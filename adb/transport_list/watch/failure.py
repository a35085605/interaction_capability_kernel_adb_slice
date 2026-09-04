from __future__ import annotations

from dataclasses import dataclass


def _normalize_optional_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchFailure:
    """Evidence that one transport-list watch failed."""

    diagnostic: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "diagnostic",
            _normalize_optional_text(
                self.diagnostic,
                field_name="ADB transport-list watch failure diagnostic",
            ),
        )


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchServerConnectionFailure(AdbTransportListWatchFailure):
    """The watch could not communicate with, or lost communication with, the ADB server."""


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchServiceFailure(AdbTransportListWatchFailure):
    """The ADB server rejected the transport-list watch service request."""


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchProtocolFailure(AdbTransportListWatchFailure):
    """The transport-list watch stream violated the expected ADB protocol."""


__all__ = [
    "AdbTransportListWatchFailure",
    "AdbTransportListWatchProtocolFailure",
    "AdbTransportListWatchServerConnectionFailure",
    "AdbTransportListWatchServiceFailure",
]
