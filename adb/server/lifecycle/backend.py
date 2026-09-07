from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.generation import AdbServerGeneration, AdbServerGenerationIssuer


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
    generation: AdbServerGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")


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
    """Evidence that the captured server generation was revoked during acquisition."""

    generation: AdbServerGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")


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

    ``generation`` is the generation that was revoked. ``acquisition`` is present only when
    that generation had committed a usable endpoint before release. Physical cleanup may still
    be in progress after logical release has advanced the backend generation.
    """

    generation: AdbServerGeneration
    acquisition: AdbServerBackendAcquired | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")
        if self.acquisition is not None:
            if not isinstance(self.acquisition, AdbServerBackendAcquired):
                raise TypeError("acquisition must be AdbServerBackendAcquired or None")
            if self.acquisition.generation != self.generation:
                raise ValueError("acquisition generation must match released generation")


@dataclass(frozen=True, slots=True)
class AdbServerBackendReleaseInactive:
    """Evidence that the matching current generation has no authority to release."""

    generation: AdbServerGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")


@dataclass(frozen=True, slots=True)
class AdbServerBackendReleaseMismatch:
    """Evidence that release did not match the backend's current server generation."""

    current: AdbServerBackendAcquired | None
    current_generation: AdbServerGeneration

    def __post_init__(self) -> None:
        if self.current is not None and not isinstance(self.current, AdbServerBackendAcquired):
            raise TypeError("current must be AdbServerBackendAcquired or None")
        if not isinstance(self.current_generation, AdbServerGeneration):
            raise TypeError("current_generation must be AdbServerGeneration")
        if self.current is not None and self.current.generation != self.current_generation:
            raise ValueError("current_generation must match current acquisition generation")


AdbServerBackendReleaseResult: TypeAlias = (
    AdbServerBackendReleased
    | AdbServerBackendReleaseInactive
    | AdbServerBackendReleaseMismatch
)


@runtime_checkable
class AdbServerBackend(Protocol):
    """Sole authority for one runtime-scoped ADB server generation.

    ``generation`` always exists and fences stale lifecycle work. It is captured by acquisition
    attempts, remains stable across failed attempts, and advances when matching pending or usable
    authority is logically released. ``current`` exists only while the current generation owns a
    usable endpoint. All views and ownership transitions are concurrency-safe and linearizable.
    """

    @property
    def generation(self) -> AdbServerGeneration:
        """Return the current server generation, including while the backend is idle."""
        ...

    @property
    def current(self) -> AdbServerBackendAcquired | None:
        """Return the currently owned usable server acquisition, if any."""
        ...

    def acquire(
        self,
        endpoint_constraint: AdbServerEndpoint | None = None,
    ) -> AdbServerBackendAcquireResult:
        """Acquire usable ADB server access within the current generation."""
        ...

    def release(self, expected: AdbServerGeneration) -> AdbServerBackendReleaseResult:
        """Release matching authority and advance the generation at logical revocation."""
        ...


class AdbServerBackendFactory(Protocol):
    """Construct one runtime-scoped ADB server backend."""

    def __call__(
        self,
        generation_issuer: AdbServerGenerationIssuer,
    ) -> AdbServerBackend:
        """Construct a backend using the runtime-scoped server generation issuer."""
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
    "AdbServerBackendReleaseInactive",
    "AdbServerBackendReleaseMismatch",
    "AdbServerBackendReleaseResult",
]
