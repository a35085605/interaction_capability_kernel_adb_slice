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


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireRevoked:
    """Evidence that a pre-issued server authority was revoked during acquisition."""

    identity: AdbServerIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.identity, AdbServerIdentity):
            raise TypeError("identity must be AdbServerIdentity")


AdbServerBackendAcquireResult: TypeAlias = (
    AdbServerBackendAcquired
    | AdbServerBackendAlreadyAcquired
    | AdbServerBackendAcquireDeferred
    | AdbServerBackendAcquireFailed
    | AdbServerBackendAcquireRevoked
)


@dataclass(frozen=True, slots=True)
class AdbServerBackendReleased:
    """Evidence that matching backend authority was released.

    ``acquisition`` is present only when the authority had committed a usable endpoint
    before release. How a pending acquisition is stopped is a backend implementation detail.
    """

    identity: AdbServerIdentity
    acquisition: AdbServerBackendAcquired | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, AdbServerIdentity):
            raise TypeError("identity must be AdbServerIdentity")
        if self.acquisition is not None:
            if not isinstance(self.acquisition, AdbServerBackendAcquired):
                raise TypeError("acquisition must be AdbServerBackendAcquired or None")
            if self.acquisition.identity != self.identity:
                raise ValueError("acquisition identity must match released identity")


@dataclass(frozen=True, slots=True)
class AdbServerBackendReleaseMismatch:
    """Evidence that release did not match the backend's current server authority."""

    current: AdbServerBackendAcquired | None
    current_identity: AdbServerIdentity | None = None

    def __post_init__(self) -> None:
        if self.current is not None and not isinstance(self.current, AdbServerBackendAcquired):
            raise TypeError("current must be AdbServerBackendAcquired or None")
        if self.current_identity is not None and not isinstance(
            self.current_identity, AdbServerIdentity
        ):
            raise TypeError("current_identity must be AdbServerIdentity or None")
        if self.current is not None:
            if self.current_identity is None:
                object.__setattr__(self, "current_identity", self.current.identity)
            elif self.current_identity != self.current.identity:
                raise ValueError("current_identity must match current acquisition identity")


AdbServerBackendReleaseResult: TypeAlias = (
    AdbServerBackendReleased | AdbServerBackendReleaseMismatch
)


@runtime_checkable
class AdbServerBackend(Protocol):
    """Sole authority for one runtime-scoped ADB server generation.

    ``identity`` exists while one server authority is pending or usable. ``current`` only
    exists once that authority has a usable endpoint. ``identity``, ``current``, ``acquire``
    and ``release`` are concurrency-safe, linearizable views or ownership transitions.
    The current authority identity fences stale lifecycle work.
    """

    @property
    def identity(self) -> AdbServerIdentity | None:
        """Return the current server authority identity, including during acquisition."""
        ...

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
        """Revoke matching authority and return authoritative release evidence."""
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
    "AdbServerBackendAcquireRevoked",
    "AdbServerBackendAlreadyAcquired",
    "AdbServerBackendAcquireResult",
    "AdbServerBackendFactory",
    "AdbServerBackendReleased",
    "AdbServerBackendReleaseMismatch",
    "AdbServerBackendReleaseResult",
]
