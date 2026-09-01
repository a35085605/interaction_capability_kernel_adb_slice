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
    """One mutually exclusive backend lifecycle operation."""

    ACQUIRE = "acquire"
    RELEASE = "release"


@dataclass(frozen=True, slots=True)
class AdbServerBackendSucceeded:
    """The requested backend operation ran and completed successfully."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


@dataclass(frozen=True, slots=True)
class AdbServerBackendSatisfied:
    """The requested backend operation was unnecessary because its target state already holds."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


@dataclass(frozen=True, slots=True)
class AdbServerBackendOperationInProgress:
    """The same requested backend operation is already in progress."""

    operation: AdbServerBackendOperation
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, AdbServerBackendOperation):
            raise TypeError("operation must be AdbServerBackendOperation")
        if self.diagnostic is not None:
            object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


@dataclass(frozen=True, slots=True)
class AdbServerBackendOperationBlocked:
    """Backend result indicating an unsatisfied operation prerequisite."""

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
    """Backend result indicating an unsuccessful completed operation attempt."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


AdbServerBackendResult: TypeAlias = (
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
    """Own acquisition and release of one usable ADB server attachment and report lifecycle
    operation outcomes.
    """

    def acquire(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerBackendResult:
        """Acquire a usable attachment, optionally constrained to ``endpoint``."""
        ...

    def release(self, endpoint: AdbServerEndpoint) -> AdbServerBackendResult:
        """Release the backend attachment identified by ``endpoint``."""
        ...


__all__ = [
    "AdbServerBackend",
    "AdbServerBackendFailed",
    "AdbServerBackendOperation",
    "AdbServerBackendOperationBlocked",
    "AdbServerBackendOperationInProgress",
    "AdbServerBackendResult",
    "AdbServerBackendSatisfied",
    "AdbServerBackendSucceeded",
]
