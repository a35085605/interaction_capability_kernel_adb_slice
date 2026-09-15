from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias


def _normalize_optional_diagnostic(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("diagnostic must be a string or None")
    normalized = value.strip()
    if not normalized:
        raise ValueError("diagnostic cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class _AdbTcpTransportControlFailure:
    """Base evidence for an unsuccessful explicit ADB TCP transport control command."""

    diagnostic: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "diagnostic",
            _normalize_optional_diagnostic(self.diagnostic),
        )


@dataclass(frozen=True, slots=True)
class AdbTcpTransportControlFailed(_AdbTcpTransportControlFailure):
    """The explicit ADB TCP transport control command failed."""


@dataclass(frozen=True, slots=True)
class AdbTcpTransportControlTimedOut(_AdbTcpTransportControlFailure):
    """The explicit ADB TCP transport control command exceeded its execution timeout."""


AdbTcpTransportControlFailure: TypeAlias = (
    AdbTcpTransportControlFailed | AdbTcpTransportControlTimedOut
)


@dataclass(frozen=True, slots=True)
class AdbTcpTransportConnectCommandSucceeded:
    """The explicit ADB TCP connect command completed successfully.

    This is command-completion evidence only. It does not assert that the transport is now
    present or ready in the authoritative transport-list projection.
    """


@dataclass(frozen=True, slots=True)
class AdbTcpTransportDisconnectCommandSucceeded:
    """The explicit ADB TCP disconnect command completed successfully.

    This is command-completion evidence only. It does not assert that the transport is now
    absent from the authoritative transport-list projection.
    """


AdbTcpTransportConnectResult: TypeAlias = (
    AdbTcpTransportConnectCommandSucceeded | AdbTcpTransportControlFailure
)
AdbTcpTransportDisconnectResult: TypeAlias = (
    AdbTcpTransportDisconnectCommandSucceeded | AdbTcpTransportControlFailure
)


__all__ = [
    "AdbTcpTransportConnectCommandSucceeded",
    "AdbTcpTransportConnectResult",
    "AdbTcpTransportControlFailed",
    "AdbTcpTransportControlFailure",
    "AdbTcpTransportControlTimedOut",
    "AdbTcpTransportDisconnectCommandSucceeded",
    "AdbTcpTransportDisconnectResult",
]
