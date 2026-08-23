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
    """Communication with the current ADB server lifetime could not be established or was lost."""


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
    """Starting a fresh ADB server failed."""


@dataclass(frozen=True, slots=True)
class AdbServerCloseUnprovenFailure(_AdbServerFailure):
    """Termination of the requested ADB server could not be proven."""


AdbServerRequestFailure: TypeAlias = (
    AdbServerConnectionFailure
    | AdbServerTimeoutFailure
    | AdbServerProtocolFailure
    | AdbServerServiceFailure
)
AdbServerLifecycleFailure: TypeAlias = (
    AdbServerProcessExitedFailure
    | AdbServerLaunchFailure
    | AdbServerCloseUnprovenFailure
)
AdbServerFailure: TypeAlias = AdbServerRequestFailure | AdbServerLifecycleFailure
AdbServerLivenessFailure: TypeAlias = (
    AdbServerConnectionFailure | AdbServerProcessExitedFailure
)
__all__ = [
    "AdbServerCloseUnprovenFailure",
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
