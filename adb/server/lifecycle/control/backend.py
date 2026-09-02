from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
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


class AdbServerBackendOperation(str, Enum):
    """One mutually exclusive backend implementation operation."""

    ACQUIRE = "acquire"
    RELEASE = "release"


@dataclass(frozen=True, slots=True)
class AdbServerBackendSucceeded:
    """A backend acquisition created a usable attachment."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


@dataclass(frozen=True, slots=True)
class AdbServerBackendSatisfied:
    """A backend acquisition found an already-usable matching attachment."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


@dataclass(frozen=True, slots=True)
class AdbServerBackendOperationInProgress:
    """The requested acquisition is already in progress."""

    operation: AdbServerBackendOperation
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, AdbServerBackendOperation):
            raise TypeError("operation must be AdbServerBackendOperation")
        if self.diagnostic is not None:
            object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


@dataclass(frozen=True, slots=True)
class AdbServerBackendOperationBlocked:
    """Backend acquisition cannot currently produce a usable attachment."""

    diagnostic: str
    blocking_operation: AdbServerBackendOperation | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))
        if self.blocking_operation is not None and not isinstance(
            self.blocking_operation,
            AdbServerBackendOperation,
        ):
            raise TypeError("blocking_operation must be AdbServerBackendOperation or None")


@dataclass(frozen=True, slots=True)
class AdbServerBackendFailed:
    """Backend acquisition failed to produce a usable attachment."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


AdbServerBackendAcquireResult: TypeAlias = (
    AdbServerBackendSucceeded
    | AdbServerBackendSatisfied
    | AdbServerBackendOperationInProgress
    | AdbServerBackendOperationBlocked
    | AdbServerBackendFailed
)


class _AdbServerBackendEndpointMismatchError(RuntimeError):
    """Ownership error for a release endpoint different from the backend-owned endpoint."""


def _require_owned_release_endpoint(
    owned: AdbServerEndpoint,
    requested: AdbServerEndpoint,
) -> None:
    """Reject release of an endpoint other than the exact backend-owned endpoint."""

    if not isinstance(owned, TcpAddress):
        raise TypeError("owned must be TcpAddress")
    if not isinstance(requested, TcpAddress):
        raise TypeError("requested must be TcpAddress")
    if owned != requested:
        raise _AdbServerBackendEndpointMismatchError(
            "requested endpoint does not identify the backend-owned ADB server endpoint"
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
    "AdbServerBackendAcquireResult",
    "AdbServerBackendFailed",
    "AdbServerBackendOperation",
    "AdbServerBackendOperationBlocked",
    "AdbServerBackendOperationInProgress",
    "AdbServerBackendSatisfied",
    "AdbServerBackendSucceeded",
]
