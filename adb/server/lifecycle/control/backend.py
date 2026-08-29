from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TypeAlias, runtime_checkable

from adb.server.address import AdbServerTcpAddress


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

    endpoint: AdbServerTcpAddress

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerTcpAddress):
            raise TypeError("endpoint must be AdbServerTcpAddress")


@dataclass(frozen=True, slots=True)
class AdbServerBackendSatisfied:
    """The requested backend operation was unnecessary because its target state already holds."""

    endpoint: AdbServerTcpAddress

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerTcpAddress):
            raise TypeError("endpoint must be AdbServerTcpAddress")


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
    """The requested backend operation cannot begin because a prerequisite is unsatisfied."""

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
    """The requested backend operation ran but did not complete successfully."""

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
    """Requested release endpoint does not match the backend-owned endpoint."""


def _require_owned_release_endpoint(
    owned: AdbServerTcpAddress,
    requested: AdbServerTcpAddress,
) -> None:
    """Reject release of an endpoint other than the exact backend-owned endpoint."""

    if not isinstance(owned, AdbServerTcpAddress):
        raise TypeError("owned must be AdbServerTcpAddress")
    if not isinstance(requested, AdbServerTcpAddress):
        raise TypeError("requested must be AdbServerTcpAddress")
    if owned != requested:
        raise _AdbServerBackendEndpointMismatchError(
            "requested endpoint does not identify the backend-owned ADB server endpoint"
        )


@runtime_checkable
class AdbServerBackend(Protocol):
    """Backend port for acquiring and releasing one usable ADB server attachment.

    Results represent success, satisfaction, contention, blocking, or failure; exceptions are
    reserved for invalid calls and ownership violations. Release relinquishes backend resources
    and need not terminate an underlying server process.
    """

    def acquire(
        self,
        endpoint: AdbServerTcpAddress | None = None,
    ) -> AdbServerBackendResult:
        """Acquire a usable attachment, optionally constrained to ``endpoint``."""
        ...

    def release(self, endpoint: AdbServerTcpAddress) -> AdbServerBackendResult:
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
