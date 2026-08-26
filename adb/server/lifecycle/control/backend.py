from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TypeAlias, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle.control.errors import AdbServerAttachmentMismatchError


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
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")


@dataclass(frozen=True, slots=True)
class AdbServerBackendSatisfied:
    """The requested backend operation was unnecessary because its target state already holds."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")


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


def require_backend_release_endpoint(
    owned: AdbServerEndpoint,
    requested: AdbServerEndpoint,
) -> None:
    """Reject release of an endpoint other than the exact backend-owned attachment."""

    if not isinstance(owned, AdbServerEndpoint):
        raise TypeError("owned must be AdbServerEndpoint")
    if not isinstance(requested, AdbServerEndpoint):
        raise TypeError("requested must be AdbServerEndpoint")
    if owned != requested:
        raise AdbServerAttachmentMismatchError(
            "requested endpoint does not identify the owned ADB server backend attachment"
        )


@runtime_checkable
class AdbServerBackend(Protocol):
    """Acquire and release one backend-scoped usable ADB server attachment.

    Every operation reports one of five lifecycle outcomes: it completed, its target state was
    already satisfied, the same operation is already in progress, a prerequisite currently blocks
    it, or an attempted operation failed.  Exceptions are reserved for invalid calls and violated
    ownership contracts.  Concrete resource ownership and cleanup semantics remain adapter-defined.
    Releasing an attachment relinquishes those backend resources; it does not imply that every
    backend must terminate an underlying ADB server process.
    """

    def acquire(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerBackendResult:
        ...

    def release(self, endpoint: AdbServerEndpoint) -> AdbServerBackendResult:
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
    "require_backend_release_endpoint",
]
