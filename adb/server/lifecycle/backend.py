from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.generation import AdbServerGeneration, AdbServerGenerationIssuer
from adb.server.state import AdbServerStateView


def _normalize_diagnostic(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("diagnostic must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("diagnostic cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class AdbServerAcquisition:
    """One runtime-scoped usable ADB server acquisition retained by the backend."""

    endpoint: AdbServerEndpoint
    generation: AdbServerGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireCommitted:
    """The requested acquisition committed as the backend's current authority."""

    acquisition: AdbServerAcquisition

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition, AdbServerAcquisition):
            raise TypeError("acquisition must be AdbServerAcquisition")


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireExisting:
    """The backend already retained a usable acquisition; no new acquisition was committed."""

    acquisition: AdbServerAcquisition

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition, AdbServerAcquisition):
            raise TypeError("acquisition must be AdbServerAcquisition")


@dataclass(frozen=True, slots=True)
class AdbServerBackendAcquireBlocked:
    """Acquisition could not proceed because conflicting backend work is currently active."""

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
class AdbServerBackendAcquireSuperseded:
    """The captured generation ceased to be current before this acquisition could commit."""

    generation: AdbServerGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")


AdbServerAcquireOutcome: TypeAlias = (
    AdbServerBackendAcquireCommitted
    | AdbServerBackendAcquireExisting
    | AdbServerBackendAcquireBlocked
    | AdbServerBackendAcquireFailed
    | AdbServerBackendAcquireSuperseded
)


@dataclass(frozen=True, slots=True)
class AdbServerBackendReleaseApplied:
    """Matching authority was released and its generation was advanced.

    ``acquisition`` is the committed acquisition that was detached. ``None`` means the released
    authority was a still-pending acquisition rather than a committed usable acquisition.
    """

    generation: AdbServerGeneration
    acquisition: AdbServerAcquisition | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")
        if self.acquisition is not None and not isinstance(
            self.acquisition, AdbServerAcquisition
        ):
            raise TypeError("acquisition must be AdbServerAcquisition or None")
        if self.acquisition is not None and self.acquisition.generation != self.generation:
            raise ValueError("released acquisition generation must match generation")


@dataclass(frozen=True, slots=True)
class AdbServerBackendReleaseInactive:
    """The matching current generation has no authority to release."""

    generation: AdbServerGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")


@dataclass(frozen=True, slots=True)
class AdbServerBackendReleaseGenerationMismatch:
    """The requested generation does not match the backend's current generation."""

    current: AdbServerAcquisition | None
    current_generation: AdbServerGeneration

    def __post_init__(self) -> None:
        if self.current is not None and not isinstance(self.current, AdbServerAcquisition):
            raise TypeError("current must be AdbServerAcquisition or None")
        if not isinstance(self.current_generation, AdbServerGeneration):
            raise TypeError("current_generation must be AdbServerGeneration")
        if self.current is not None and self.current.generation != self.current_generation:
            raise ValueError("current_generation must match current acquisition generation")


AdbServerReleaseOutcome: TypeAlias = (
    AdbServerBackendReleaseApplied
    | AdbServerBackendReleaseInactive
    | AdbServerBackendReleaseGenerationMismatch
)


@runtime_checkable
class AdbServerLifecycle(AdbServerStateView, Protocol):
    """Sole authority for one runtime-scoped ADB server generation.

    ``read()`` returns the canonical atomic state snapshot. Generation fences stale lifecycle
    work and advances when matching pending or usable authority is logically released. Physical
    cleanup ownership is an implementation concern and is not part of lifecycle outcomes.
    """

    def acquire(
        self,
        endpoint_constraint: AdbServerEndpoint | None = None,
    ) -> AdbServerAcquireOutcome:
        """Attempt to establish usable ADB server access within the current generation.

        A committed or existing constrained acquisition must expose exactly ``endpoint_constraint``.
        """
        ...

    def release(self, expected: AdbServerGeneration) -> AdbServerReleaseOutcome:
        """Release matching authority and advance the generation at logical revocation."""
        ...


class AdbServerLifecycleFactory(Protocol):
    """Construct one runtime-scoped ADB server backend."""

    def __call__(
        self,
        generation_issuer: AdbServerGenerationIssuer,
    ) -> AdbServerLifecycle:
        """Construct a backend using the runtime-scoped server generation issuer."""
        ...


__all__ = [
    "AdbServerLifecycle",
    "AdbServerAcquisition",
    "AdbServerBackendAcquireBlocked",
    "AdbServerBackendAcquireCommitted",
    "AdbServerBackendAcquireExisting",
    "AdbServerBackendAcquireFailed",
    "AdbServerAcquireOutcome",
    "AdbServerBackendAcquireSuperseded",
    "AdbServerLifecycleFactory",
    "AdbServerBackendReleaseApplied",
    "AdbServerBackendReleaseGenerationMismatch",
    "AdbServerBackendReleaseInactive",
    "AdbServerReleaseOutcome",
]
