from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint


def _normalize_diagnostic(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("diagnostic must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("diagnostic cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireSucceeded:
    """A backend acquisition created a usable attachment."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireSatisfied:
    """A backend acquisition found an already-usable matching attachment."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireInProgress:
    """The requested acquisition is already in progress."""

    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if self.diagnostic is not None:
            object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireBlocked:
    """Backend acquisition cannot currently produce a usable attachment."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireFailed:
    """Backend acquisition failed to produce a usable attachment."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


AdbServerBackendAcquireResult: TypeAlias = (
    AdbServerBackendAcquireSucceeded
    | AdbServerBackendAcquireSatisfied
    | AdbServerBackendAcquireInProgress
    | AdbServerBackendAcquireBlocked
    | AdbServerBackendAcquireFailed
)


@runtime_checkable
class AdbServerBackend(Protocol):
    """Own acquisition and physical convergence of one usable ADB server attachment."""

    def acquire(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerBackendAcquireResult:
        """Acquire a usable attachment, optionally constrained to ``endpoint``."""
        ...

    def release(self, endpoint: AdbServerEndpoint) -> None:
        """Accept relinquishment of ``endpoint`` and own its physical cleanup convergence."""
        ...


__all__ = [
    "AdbServerBackend",
    "AdbServerBackendAcquireBlocked",
    "AdbServerBackendAcquireFailed",
    "AdbServerBackendAcquireInProgress",
    "AdbServerBackendAcquireResult",
    "AdbServerBackendAcquireSatisfied",
    "AdbServerBackendAcquireSucceeded",
]
