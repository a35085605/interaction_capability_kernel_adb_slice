from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Generic, TypeAlias, TypeVar

from adb._lifecycle.resource import ResourceScope
from adb._lifecycle.snapshot import LifecycleSnapshot


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")
FailureT = TypeVar("FailureT")


def _normalize_diagnostic(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("diagnostic must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("diagnostic cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True, eq=False)
class AcquireAttempt(Generic[GenerationT]):
    """One in-flight acquisition attempt and its captured lifecycle context."""

    generation: GenerationT
    cancellation: Event
    resource_scope: ResourceScope


@dataclass(frozen=True, slots=True)
class AcquireStartExisting(Generic[GenerationT, AccessT]):
    """Acquire cannot start because the lifecycle already retains usable access.

    The snapshot is captured atomically while the lifecycle lock is held. Domain request constraints
    have not yet been checked.
    """

    snapshot: LifecycleSnapshot[GenerationT, AccessT]

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, LifecycleSnapshot):
            raise TypeError("snapshot must be LifecycleSnapshot")
        if self.snapshot.access is None:
            raise TypeError("snapshot access cannot be None")


@dataclass(frozen=True, slots=True)
class AcquireStartBusy:
    """Acquire cannot start because another attempt is still in flight."""

    draining: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.draining, bool):
            raise TypeError("draining must be bool")


@dataclass(frozen=True, slots=True)
class AcquireStartBlocked:
    """Acquire cannot start because a kernel/domain precondition currently blocks it."""

    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if self.diagnostic is None:
            return
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


AcquireStartResult: TypeAlias = (
    AcquireAttempt[GenerationT]
    | AcquireStartExisting[GenerationT, AccessT]
    | AcquireStartBusy
    | AcquireStartBlocked
)


@dataclass(frozen=True, slots=True)
class AcquireCommitted(Generic[GenerationT, AccessT]):
    """The requested access committed as the lifecycle's current authority."""

    snapshot: LifecycleSnapshot[GenerationT, AccessT]

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, LifecycleSnapshot):
            raise TypeError("snapshot must be LifecycleSnapshot")
        if self.snapshot.access is None:
            raise TypeError("snapshot access cannot be None")


@dataclass(frozen=True, slots=True)
class AcquireExisting(Generic[GenerationT, AccessT]):
    """Existing lifecycle access satisfies the completed acquire request."""

    snapshot: LifecycleSnapshot[GenerationT, AccessT]

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, LifecycleSnapshot):
            raise TypeError("snapshot must be LifecycleSnapshot")
        if self.snapshot.access is None:
            raise TypeError("snapshot access cannot be None")


@dataclass(frozen=True, slots=True)
class AcquireBlocked:
    """The completed acquire request cannot currently proceed."""

    diagnostic: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _normalize_diagnostic(self.diagnostic))


@dataclass(frozen=True, slots=True)
class AcquireFailed(Generic[FailureT]):
    """The acquire request failed with a domain-specific typed failure."""

    failure: FailureT

    def __post_init__(self) -> None:
        if self.failure is None:
            raise TypeError("failure cannot be None")


@dataclass(frozen=True, slots=True)
class AcquireSuperseded(Generic[GenerationT]):
    """The captured generation ceased to be current before access could commit."""

    generation: GenerationT

    def __post_init__(self) -> None:
        if self.generation is None:
            raise TypeError("generation cannot be None")


@dataclass(frozen=True, slots=True)
class ReleaseGenerationMismatch(Generic[GenerationT, AccessT]):
    current_generation: GenerationT
    access: AccessT | None

    def __post_init__(self) -> None:
        if self.current_generation is None:
            raise TypeError("current_generation cannot be None")


@dataclass(frozen=True, slots=True)
class ReleaseInactive(Generic[GenerationT]):
    """No current access; a revoked acquisition may still be draining."""

    generation: GenerationT

    def __post_init__(self) -> None:
        if self.generation is None:
            raise TypeError("generation cannot be None")


@dataclass(frozen=True, slots=True)
class ReleaseAcquisitionRevoked(Generic[GenerationT]):
    """Matching in-flight acquisition lost commit authority and is now draining."""

    generation: GenerationT

    def __post_init__(self) -> None:
        if self.generation is None:
            raise TypeError("generation cannot be None")


@dataclass(frozen=True, slots=True)
class ReleaseAccessDetached(Generic[GenerationT, AccessT]):
    """Matching access was detached from the current lifecycle state."""

    generation: GenerationT
    access: AccessT

    def __post_init__(self) -> None:
        if self.generation is None:
            raise TypeError("generation cannot be None")
        if self.access is None:
            raise TypeError("access cannot be None")


ReleaseResult: TypeAlias = (
    ReleaseGenerationMismatch[GenerationT, AccessT]
    | ReleaseInactive[GenerationT]
    | ReleaseAcquisitionRevoked[GenerationT]
    | ReleaseAccessDetached[GenerationT, AccessT]
)


class CleanupRegistrationError(RuntimeError):
    """Cleanup registration failed after the lifecycle transition was already applied."""

    def __init__(self, outcome: ReleaseAccessDetached) -> None:
        self.outcome = outcome
        super().__init__("lifecycle transition applied, but cleanup registration failed")


__all__ = [
    "AcquireAttempt",
    "AcquireBlocked",
    "AcquireCommitted",
    "AcquireExisting",
    "AcquireFailed",
    "AcquireStartBlocked",
    "AcquireStartBusy",
    "AcquireStartExisting",
    "AcquireStartResult",
    "AcquireSuperseded",
    "CleanupRegistrationError",
    "ReleaseAccessDetached",
    "ReleaseAcquisitionRevoked",
    "ReleaseGenerationMismatch",
    "ReleaseInactive",
    "ReleaseResult",
]
