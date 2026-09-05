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
class AdbServerBackendAcquireAchieved:
    """The requested acquisition became satisfied during this call.

    This call established a new acquisition for ``endpoint``. Only this result authorizes the
    lifecycle coordinator to attempt a corresponding authoritative-state activation.
    """

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquirePreexisting:
    """The requested acquisition existed before this call.

    This call did not establish a new acquisition and therefore does not authorize a corresponding
    authoritative-state activation.
    """

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


# Compatibility alias for callers using the former result name. New code should use
# ``AdbServerBackendAcquirePreexisting`` so the result is not mistaken for acquisition success.
AdbServerBackendAcquireAlreadySatisfied = AdbServerBackendAcquirePreexisting


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireInProgress:
    """Backend acquisition work is already in progress.

    This call did not establish a new acquisition.
    """

    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if self.diagnostic is not None:
            object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireBlocked:
    """Backend acquisition is currently unable to satisfy the request."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireFailed:
    """Backend acquisition failed to satisfy the request."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


AdbServerBackendAcquireResult: TypeAlias = (
    AdbServerBackendAcquireAchieved
    | AdbServerBackendAcquirePreexisting
    | AdbServerBackendAcquireInProgress
    | AdbServerBackendAcquireBlocked
    | AdbServerBackendAcquireFailed
)


@runtime_checkable
class AdbServerBackend(Protocol):
    """Provide acquisition and relinquishment of usable ADB server access.

    Lifecycle coordination does not impose total ordering across backend effects. Calls may overlap;
    implementations must make each acquire/release effect concurrency-safe and independently
    linearizable.
    """

    def acquire(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerBackendAcquireResult:
        """Acquire usable ADB server access, optionally constrained to ``endpoint``."""
        ...

    def release(self, endpoint: AdbServerEndpoint) -> None:
        """Relinquish the backend acquisition for ``endpoint``.

        Relinquishment is complete at this boundary when the backend accepts responsibility for release;
        implementation-specific cleanup may continue afterward and is not part of the lifecycle result.
        """
        ...


__all__ = [
    "AdbServerBackend",
    "AdbServerBackendAcquireBlocked",
    "AdbServerBackendAcquireFailed",
    "AdbServerBackendAcquireInProgress",
    "AdbServerBackendAcquireResult",
    "AdbServerBackendAcquirePreexisting",
    "AdbServerBackendAcquireAlreadySatisfied",
    "AdbServerBackendAcquireAchieved",
]
