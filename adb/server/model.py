from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral
from typing import TypeAlias


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
    """Immutable evidence from one specific ADB server failure boundary."""

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
    """The current ADB server lifetime lost a required transport connection."""


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
    """The exact process-owned native ADB server lifetime exited."""


@dataclass(frozen=True, slots=True)
class AdbServerLaunchFailure(_AdbServerFailure):
    """Creation of one fresh process-owned ADB server lifetime failed."""


@dataclass(frozen=True, slots=True)
class AdbServerCloseUnprovenFailure(_AdbServerFailure):
    """Termination of one exact owned ADB server lifetime could not be proven."""


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
AdbServerOwnershipLossFailure: TypeAlias = (
    AdbServerConnectionFailure | AdbServerProcessExitedFailure
)


__all__ = [
    "AdbServerEndpoint",
    "AdbServerCloseUnprovenFailure",
    "AdbServerConnectionFailure",
    "AdbServerFailure",
    "AdbServerLaunchFailure",
    "AdbServerLifecycleFailure",
    "AdbServerOwnershipLossFailure",
    "AdbServerProcessExitedFailure",
    "AdbServerProtocolFailure",
    "AdbServerRequestFailure",
    "AdbServerServiceFailure",
    "AdbServerTimeoutFailure",
]
