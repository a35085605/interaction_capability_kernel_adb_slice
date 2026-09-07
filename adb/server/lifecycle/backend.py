from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity, AdbServerIdentityIssuer


def _normalize_diagnostic(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("diagnostic must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("diagnostic cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquired:
    """One runtime-scoped usable ADB server acquisition retained by the backend."""

    endpoint: AdbServerEndpoint
    identity: AdbServerIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if not isinstance(self.identity, AdbServerIdentity):
            raise TypeError("identity must be AdbServerIdentity")


@dataclass(frozen=True, slots=True)
class AdbServerBackendAlreadyAcquired:
    """Evidence that the backend already retains this usable server acquisition."""

    acquisition: AdbServerBackendAcquired

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition, AdbServerBackendAcquired):
            raise TypeError("acquisition must be AdbServerBackendAcquired")


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireDeferred:
    """Backend acquisition could not begin because another backend operation is active."""

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
    AdbServerBackendAcquired
    | AdbServerBackendAlreadyAcquired
    | AdbServerBackendAcquireDeferred
    | AdbServerBackendAcquireFailed
)


@dataclass(frozen=True, slots=True)
class AdbServerBackendReleased:
    """Evidence that matching backend ownership was released."""

    acquisition: AdbServerBackendAcquired

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition, AdbServerBackendAcquired):
            raise TypeError("acquisition must be AdbServerBackendAcquired")


@dataclass(frozen=True, slots=True)
class AdbServerBackendReleaseMismatch:
    """Evidence that release did not match the backend's current acquisition."""

    current: AdbServerBackendAcquired | None

    def __post_init__(self) -> None:
        if self.current is not None and not isinstance(self.current, AdbServerBackendAcquired):
            raise TypeError("current must be AdbServerBackendAcquired or None")


AdbServerBackendReleaseResult: TypeAlias = (
    AdbServerBackendReleased | AdbServerBackendReleaseMismatch
)


@runtime_checkable
class AdbServerBackend(Protocol):
    """Sole authority for one runtime-scoped usable ADB server acquisition.

    ``current``, ``acquire`` and ``release`` are concurrency-safe, linearizable views or
    ownership transitions. The current acquisition's identity fences stale lifecycle work.
    """

    @property
    def current(self) -> AdbServerBackendAcquired | None:
        """Return the currently owned usable server acquisition, if any."""
        ...

    def acquire(
        self,
        endpoint_constraint: AdbServerEndpoint | None = None,
    ) -> AdbServerBackendAcquireResult:
        """Acquire usable ADB server access, optionally constrained to ``endpoint_constraint``."""
        ...

    def release(self, expected: AdbServerIdentity) -> AdbServerBackendReleaseResult:
        """Release current ownership only when its identity matches ``expected``."""
        ...


class AdbServerBackendFactory(Protocol):
    """Construct one runtime-scoped ADB server backend."""

    def __call__(
        self,
        identity_issuer: AdbServerIdentityIssuer,
    ) -> AdbServerBackend:
        """Construct a backend using the runtime-scoped server identity issuer."""
        ...


__all__ = [
    "AdbServerBackend",
    "AdbServerBackendAcquired",
    "AdbServerBackendAcquireDeferred",
    "AdbServerBackendAcquireFailed",
    "AdbServerBackendAlreadyAcquired",
    "AdbServerBackendAcquireResult",
    "AdbServerBackendFactory",
    "AdbServerBackendReleased",
    "AdbServerBackendReleaseMismatch",
    "AdbServerBackendReleaseResult",
]
