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
class AdbServerAcquireSucceeded:
    """A fresh usable backend attachment was acquired."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")


@dataclass(frozen=True, slots=True)
class AdbServerAcquireSatisfied:
    """The backend already owns the usable attachment requested by acquire()."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")


@dataclass(frozen=True, slots=True)
class AdbServerBackendOperationInProgress:
    """A backend lifecycle operation or unresolved cleanup is still converging."""

    operation: AdbServerBackendOperation
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, AdbServerBackendOperation):
            raise TypeError("operation must be AdbServerBackendOperation")
        if self.diagnostic is not None:
            object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


@dataclass(frozen=True, slots=True)
class AdbServerAcquireFailed:
    """A backend acquire operation started but did not produce a usable attachment."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


AdbServerAcquireResult: TypeAlias = (
    AdbServerAcquireSucceeded
    | AdbServerAcquireSatisfied
    | AdbServerBackendOperationInProgress
    | AdbServerAcquireFailed
)


@dataclass(frozen=True, slots=True)
class AdbServerReleaseSucceeded:
    """The exact staged backend attachment was released."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")


@dataclass(frozen=True, slots=True)
class AdbServerReleaseNotStaged:
    """release() targeted an endpoint while no backend attachment was staged."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")


@dataclass(frozen=True, slots=True)
class AdbServerReleaseFailed:
    """A backend release operation started but did not complete successfully."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


AdbServerReleaseResult: TypeAlias = (
    AdbServerReleaseSucceeded
    | AdbServerBackendOperationInProgress
    | AdbServerReleaseNotStaged
    | AdbServerReleaseFailed
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

    Expected lifecycle outcomes are returned as result values.  Exceptions are reserved for
    invalid calls and violated ownership contracts.  Concrete resource ownership, operation
    exclusion, and cleanup semantics remain adapter-defined.  Releasing an attachment relinquishes
    those backend resources; it does not imply that every backend must terminate an underlying
    ADB server process.
    """

    def acquire(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerAcquireResult:
        ...

    def release(self, endpoint: AdbServerEndpoint) -> AdbServerReleaseResult:
        ...


__all__ = [
    "AdbServerAcquireFailed",
    "AdbServerAcquireResult",
    "AdbServerAcquireSatisfied",
    "AdbServerAcquireSucceeded",
    "AdbServerBackend",
    "AdbServerBackendOperation",
    "AdbServerBackendOperationInProgress",
    "AdbServerReleaseFailed",
    "AdbServerReleaseNotStaged",
    "AdbServerReleaseResult",
    "AdbServerReleaseSucceeded",
    "require_backend_release_endpoint",
]
