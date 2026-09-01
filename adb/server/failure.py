from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias


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
class _AdbServerFailure:
    """Evidence of an ADB server failure."""

    diagnostic: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "diagnostic",
            _normalize_optional_text(
                self.diagnostic,
                field_name="ADB server failure diagnostic",
            ),
        )


@dataclass(frozen=True, slots=True)
class AdbServerConnectionFailure(_AdbServerFailure):
    """Evidence of unavailable or lost communication with the current ADB server lifetime."""


@dataclass(frozen=True, slots=True)
class AdbServerTimeoutFailure(_AdbServerFailure):
    """One bounded ADB server operation exceeded its timeout."""


@dataclass(frozen=True, slots=True)
class AdbServerProtocolFailure(_AdbServerFailure):
    """ADB framing or payload data violated the expected protocol."""


@dataclass(frozen=True, slots=True)
class AdbServerServiceFailure(_AdbServerFailure):
    """The ADB server rejected one service request."""


@dataclass(frozen=True, slots=True)
class AdbServerProcessExitedFailure(_AdbServerFailure):
    """The observed ADB server process exited."""


@dataclass(frozen=True, slots=True)
class AdbServerLaunchFailure(_AdbServerFailure):
    """Acquiring a fresh usable ADB server attachment failed."""


AdbServerRequestFailure: TypeAlias = (
    AdbServerConnectionFailure
    | AdbServerTimeoutFailure
    | AdbServerProtocolFailure
    | AdbServerServiceFailure
)
AdbServerLifecycleFailure: TypeAlias = (
    AdbServerProcessExitedFailure | AdbServerLaunchFailure
)
AdbServerFailure: TypeAlias = AdbServerRequestFailure | AdbServerLifecycleFailure
AdbServerLivenessFailure: TypeAlias = (
    AdbServerConnectionFailure | AdbServerProcessExitedFailure
)


__all__ = [
    "AdbServerConnectionFailure",
    "AdbServerFailure",
    "AdbServerLaunchFailure",
    "AdbServerLifecycleFailure",
    "AdbServerLivenessFailure",
    "AdbServerProcessExitedFailure",
    "AdbServerProtocolFailure",
    "AdbServerRequestFailure",
    "AdbServerServiceFailure",
    "AdbServerTimeoutFailure",
]
