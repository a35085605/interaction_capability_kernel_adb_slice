from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from adb.server.endpoint import AdbServerEndpoint


def _normalize_optional_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


class AdbServerAvailability(str, Enum):
    """Observed availability of the process-owned ADB server endpoint."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INDETERMINATE = "indeterminate"


class AdbServerFailureKind(str, Enum):
    """Typed cause carried as server failure evidence instead of availability state."""

    CONNECTION = "connection"
    TIMEOUT = "timeout"
    PROTOCOL = "protocol"
    SERVICE = "service"
    PROCESS_EXITED = "process_exited"
    LAUNCH = "launch"
    CLOSE_UNPROVEN = "close_unproven"


@dataclass(frozen=True, slots=True)
class AdbServerFailure:
    """Immutable evidence describing why one server observation or lifecycle step failed."""

    kind: AdbServerFailureKind
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AdbServerFailureKind):
            raise TypeError("kind must be AdbServerFailureKind")
        object.__setattr__(
            self,
            "diagnostic",
            _normalize_optional_text(
                self.diagnostic,
                field_name="ADB server failure diagnostic",
            ),
        )


@dataclass(frozen=True, slots=True)
class AdbServerObservation:
    """Availability projection paired with the evidence that supports a negative conclusion."""

    endpoint: AdbServerEndpoint
    availability: AdbServerAvailability
    failure: AdbServerFailure | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if not isinstance(self.availability, AdbServerAvailability):
            raise TypeError("availability must be AdbServerAvailability")
        if self.failure is not None and not isinstance(self.failure, AdbServerFailure):
            raise TypeError("failure must be AdbServerFailure or None")
        if self.availability is AdbServerAvailability.AVAILABLE and self.failure is not None:
            raise ValueError("available server observation cannot carry failure evidence")
        if self.availability is AdbServerAvailability.UNAVAILABLE and self.failure is None:
            raise ValueError("unavailable server observation requires failure evidence")


__all__ = [
    "AdbServerAvailability",
    "AdbServerFailure",
    "AdbServerFailureKind",
    "AdbServerObservation",
]
