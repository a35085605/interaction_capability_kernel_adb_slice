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
class AdbTransportListWatchAcquisition:
    """One runtime-scoped usable transport-list watch retained by the lifecycle.

    This is lifecycle evidence only. The lifecycle retains the physical watch resource; producer
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
class AdbTransportListWatchAcquireCommitted:
    """The requested watch acquisition committed as the lifecycle's current authority."""

    acquisition: AdbTransportListWatchAcquisition

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition, AdbTransportListWatchAcquisition):
            raise TypeError("acquisition must be AdbTransportListWatchAcquisition")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchAcquireExisting:
    """The lifecycle already retained a usable watch acquisition; no new one was committed."""

    acquisition: AdbTransportListWatchAcquisition

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition, AdbTransportListWatchAcquisition):
            raise TypeError("acquisition must be AdbTransportListWatchAcquisition")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchAcquireBlocked:
    """Watch acquisition could not proceed because conflicting lifecycle work is active."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchAcquireFailed:
    """Expected failure to establish a usable watch acquisition."""

    failure: AdbTransportListWatchFailure

    def __post_init__(self) -> None:
        if not isinstance(self.failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchAcquireSuperseded:
    """The captured watch generation ceased to be current before the acquisition could commit."""

    generation: AdbTransportListWatchGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")


AdbTransportListWatchAcquireOutcome: TypeAlias = (
    AdbTransportListWatchAcquireCommitted
    | AdbTransportListWatchAcquireExisting
    | AdbTransportListWatchAcquireBlocked
    | AdbTransportListWatchAcquireFailed
    | AdbTransportListWatchAcquireSuperseded
)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchReleaseApplied:
    """Matching watch authority was released and its generation was advanced.

    ``acquisition`` is the committed acquisition that was detached. ``None`` means the released
    authority was a still-pending acquisition rather than a committed usable watch.
    """

    generation: AdbTransportListWatchGeneration
    acquisition: AdbTransportListWatchAcquisition | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")
        if self.acquisition is not None and not isinstance(
            self.acquisition, AdbTransportListWatchAcquisition
        ):
            raise TypeError(
                "acquisition must be AdbTransportListWatchAcquisition or None"
            )
        if self.acquisition is not None and self.acquisition.generation != self.generation:
            raise ValueError("released acquisition generation must match generation")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchReleaseInactive:
    """The matching current generation has no watch authority to release.

    An acquisition from a revoked generation may still be draining and block a new acquire.
    """

    generation: AdbTransportListWatchGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchReleaseGenerationMismatch:
    """The requested generation does not match the lifecycle's current watch generation."""

    current: AdbTransportListWatchAcquisition | None
    current_generation: AdbTransportListWatchGeneration

    def __post_init__(self) -> None:
        if self.current is not None and not isinstance(
            self.current, AdbTransportListWatchAcquisition
        ):
            raise TypeError("current must be AdbTransportListWatchAcquisition or None")
        if not isinstance(self.current_generation, AdbTransportListWatchGeneration):
            raise TypeError("current_generation must be AdbTransportListWatchGeneration")
        if self.current is not None and self.current.generation != self.current_generation:
            raise ValueError("current_generation must match current acquisition generation")


AdbTransportListWatchReleaseOutcome: TypeAlias = (
    AdbTransportListWatchReleaseApplied
    | AdbTransportListWatchReleaseInactive
    | AdbTransportListWatchReleaseGenerationMismatch
)


@runtime_checkable
class AdbTransportListWatchLifecycle(AdbTransportListWatchStateView, Protocol):
    """Sole authority for one runtime-scoped transport-list watch generation.

    ``read()`` returns the canonical atomic state snapshot. Generation fences stale lifecycle
    work and advances when matching pending or usable authority is logically released. Physical
    watch resources, cleanup ownership, and producer data-plane plumbing remain implementation
    details and are not part of lifecycle outcomes.
    """

    def acquire(
        self,
        endpoint: TcpAddress,
    ) -> AdbTransportListWatchAcquireOutcome:
        """Attempt to establish one fully usable watch within the current generation."""
        ...

    def release(
        self,
        expected: AdbTransportListWatchGeneration,
    ) -> AdbTransportListWatchReleaseOutcome:
        """Release matching authority and advance generation at logical revocation."""
        ...


class AdbTransportListWatchLifecycleFactory(Protocol):
    """Construct one runtime-scoped transport-list watch lifecycle."""

    def __call__(
        self,
        generation_issuer: AdbTransportListWatchGenerationIssuer,
    ) -> AdbTransportListWatchLifecycle:
        ...


__all__ = [
    "AdbTransportListWatchLifecycle",
    "AdbTransportListWatchAcquisition",
    "AdbTransportListWatchAcquireBlocked",
    "AdbTransportListWatchAcquireCommitted",
    "AdbTransportListWatchAcquireExisting",
    "AdbTransportListWatchAcquireFailed",
    "AdbTransportListWatchAcquireOutcome",
    "AdbTransportListWatchAcquireSuperseded",
    "AdbTransportListWatchLifecycleFactory",
    "AdbTransportListWatchReleaseApplied",
    "AdbTransportListWatchReleaseGenerationMismatch",
    "AdbTransportListWatchReleaseInactive",
    "AdbTransportListWatchReleaseOutcome",
]
