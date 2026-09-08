from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress

from adb.transport_list.watch.failure import AdbTransportListWatchFailure
from adb.transport_list.watch.generation import (
    AdbTransportListWatchGeneration,
    AdbTransportListWatchGenerationIssuer,
)
from adb.transport_list.watch.state import AdbTransportListWatchStateView


def _normalize_diagnostic(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("diagnostic must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("diagnostic cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendAcquisition:
    """One runtime-scoped usable transport-list watch retained by the backend.

    This is lifecycle evidence only. The backend retains the physical watch resource; producer
    access to transport-list data is provided through a separate watch-stream capability.
    """

    endpoint: TcpAddress
    generation: AdbTransportListWatchGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendAcquireCommitted:
    """The requested watch acquisition committed as the backend's current authority."""

    acquisition: AdbTransportListWatchBackendAcquisition

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition, AdbTransportListWatchBackendAcquisition):
            raise TypeError("acquisition must be AdbTransportListWatchBackendAcquisition")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendAcquireExisting:
    """The backend already retained a usable watch acquisition; no new one was committed."""

    acquisition: AdbTransportListWatchBackendAcquisition

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition, AdbTransportListWatchBackendAcquisition):
            raise TypeError("acquisition must be AdbTransportListWatchBackendAcquisition")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendAcquireBlocked:
    """Watch acquisition could not proceed because conflicting backend work is active."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendAcquireFailed:
    """Expected failure to establish a usable watch acquisition."""

    failure: AdbTransportListWatchFailure

    def __post_init__(self) -> None:
        if not isinstance(self.failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendAcquireSuperseded:
    """The captured watch generation ceased to be current before the acquisition could commit."""

    generation: AdbTransportListWatchGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")


AdbTransportListWatchBackendAcquireOutcome: TypeAlias = (
    AdbTransportListWatchBackendAcquireCommitted
    | AdbTransportListWatchBackendAcquireExisting
    | AdbTransportListWatchBackendAcquireBlocked
    | AdbTransportListWatchBackendAcquireFailed
    | AdbTransportListWatchBackendAcquireSuperseded
)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendReleaseApplied:
    """Matching watch authority was released and its generation was advanced.

    ``acquisition`` is the committed acquisition that was detached. ``None`` means the released
    authority was a still-pending acquisition rather than a committed usable watch.
    """

    generation: AdbTransportListWatchGeneration
    acquisition: AdbTransportListWatchBackendAcquisition | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")
        if self.acquisition is not None and not isinstance(
            self.acquisition, AdbTransportListWatchBackendAcquisition
        ):
            raise TypeError(
                "acquisition must be AdbTransportListWatchBackendAcquisition or None"
            )
        if self.acquisition is not None and self.acquisition.generation != self.generation:
            raise ValueError("released acquisition generation must match generation")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendReleaseInactive:
    """The matching current generation has no watch authority to release."""

    generation: AdbTransportListWatchGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchBackendReleaseGenerationMismatch:
    """The requested generation does not match the backend's current watch generation."""

    current: AdbTransportListWatchBackendAcquisition | None
    current_generation: AdbTransportListWatchGeneration

    def __post_init__(self) -> None:
        if self.current is not None and not isinstance(
            self.current, AdbTransportListWatchBackendAcquisition
        ):
            raise TypeError("current must be AdbTransportListWatchBackendAcquisition or None")
        if not isinstance(self.current_generation, AdbTransportListWatchGeneration):
            raise TypeError("current_generation must be AdbTransportListWatchGeneration")
        if self.current is not None and self.current.generation != self.current_generation:
            raise ValueError("current_generation must match current acquisition generation")


AdbTransportListWatchBackendReleaseOutcome: TypeAlias = (
    AdbTransportListWatchBackendReleaseApplied
    | AdbTransportListWatchBackendReleaseInactive
    | AdbTransportListWatchBackendReleaseGenerationMismatch
)


@runtime_checkable
class AdbTransportListWatchBackend(AdbTransportListWatchStateView, Protocol):
    """Sole authority for one runtime-scoped transport-list watch generation.

    ``read()`` returns the canonical atomic state snapshot. Generation fences stale lifecycle
    work and advances when matching pending or usable authority is logically released. Physical
    watch resources, cleanup ownership, and producer data-plane plumbing remain implementation
    details and are not part of lifecycle outcomes.
    """

    def acquire(
        self,
        endpoint: TcpAddress,
    ) -> AdbTransportListWatchBackendAcquireOutcome:
        """Attempt to establish one fully usable watch within the current generation."""
        ...

    def release(
        self,
        expected: AdbTransportListWatchGeneration,
    ) -> AdbTransportListWatchBackendReleaseOutcome:
        """Release matching authority and advance generation at logical revocation."""
        ...


class AdbTransportListWatchBackendFactory(Protocol):
    """Construct one runtime-scoped transport-list watch backend."""

    def __call__(
        self,
        generation_issuer: AdbTransportListWatchGenerationIssuer,
    ) -> AdbTransportListWatchBackend:
        ...


__all__ = [
    "AdbTransportListWatchBackend",
    "AdbTransportListWatchBackendAcquisition",
    "AdbTransportListWatchBackendAcquireBlocked",
    "AdbTransportListWatchBackendAcquireCommitted",
    "AdbTransportListWatchBackendAcquireExisting",
    "AdbTransportListWatchBackendAcquireFailed",
    "AdbTransportListWatchBackendAcquireOutcome",
    "AdbTransportListWatchBackendAcquireSuperseded",
    "AdbTransportListWatchBackendFactory",
    "AdbTransportListWatchBackendReleaseApplied",
    "AdbTransportListWatchBackendReleaseGenerationMismatch",
    "AdbTransportListWatchBackendReleaseInactive",
    "AdbTransportListWatchBackendReleaseOutcome",
]
