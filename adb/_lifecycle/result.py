from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Generic, TypeAlias, TypeVar

from adb._lifecycle.resource import ResourceScope
from adb._lifecycle.snapshot import Snapshot


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")
CapabilityT = TypeVar("CapabilityT")
FailureT = TypeVar("FailureT")


def _normalize_diagnostic(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("diagnostic must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("diagnostic cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True, eq=False)
class AcquireAttempt(Generic[GenerationT, AccessT]):
    """One in-flight acquisition attempt and its captured lifecycle context."""

    generation: GenerationT
    access: AccessT
    cancellation: Event
    resource_scope: ResourceScope

    def __post_init__(self) -> None:
        if self.generation is None:
            raise TypeError("generation cannot be None")
        if self.access is None:
            raise TypeError("access cannot be None")
        if not isinstance(self.cancellation, Event):
            raise TypeError("cancellation must be Event")
        if not isinstance(self.resource_scope, ResourceScope):
            raise TypeError("resource_scope must be ResourceScope")


@dataclass(frozen=True, slots=True)
class AcquireStartExisting(Generic[GenerationT, AccessT, CapabilityT]):
    """Acquire cannot start because the lifecycle already retains usable access.

    The snapshot is captured atomically while the lifecycle lock is held. Domain request constraints
    may still decide that the requested access differs from the captured committed access.
    """

    snapshot: Snapshot[GenerationT, AccessT, CapabilityT]

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, Snapshot):
            raise TypeError("snapshot must be Snapshot")
        if self.snapshot.access is None or self.snapshot.capability is None:
            raise TypeError("existing snapshot must be committed")


@dataclass(frozen=True, slots=True)
class AcquireGenerationMismatch(Generic[GenerationT, AccessT, CapabilityT]):
    """The caller's expected generation is stale.

    ``snapshot`` is the atomic current committed-state view captured at the mismatch decision.
    """

    snapshot: Snapshot[GenerationT, AccessT, CapabilityT]

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, Snapshot):
            raise TypeError("snapshot must be Snapshot")


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
    AcquireAttempt[GenerationT, AccessT]
    | AcquireStartExisting[GenerationT, AccessT, CapabilityT]
    | AcquireGenerationMismatch[GenerationT, AccessT, CapabilityT]
    | AcquireStartBusy
    | AcquireStartBlocked
)


@dataclass(frozen=True, slots=True)
class AcquireCommitted(Generic[GenerationT, AccessT, CapabilityT]):
    """The requested access committed as the lifecycle's current authority."""

    snapshot: Snapshot[GenerationT, AccessT, CapabilityT]

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, Snapshot):
            raise TypeError("snapshot must be Snapshot")
        if self.snapshot.access is None or self.snapshot.capability is None:
            raise TypeError("committed snapshot must contain access and capability")


@dataclass(frozen=True, slots=True)
class AcquireExisting(Generic[GenerationT, AccessT, CapabilityT]):
    """Existing lifecycle access satisfies the completed acquire request."""

    snapshot: Snapshot[GenerationT, AccessT, CapabilityT]

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, Snapshot):
            raise TypeError("snapshot must be Snapshot")
        if self.snapshot.access is None or self.snapshot.capability is None:
            raise TypeError("existing snapshot must contain access and capability")


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
    """The caller's expected generation does not match current lifecycle authority."""

    current_generation: GenerationT
    access: AccessT | None

    def __post_init__(self) -> None:
        if self.current_generation is None:
            raise TypeError("current_generation cannot be None")


@dataclass(frozen=True, slots=True)
class ReleaseAccessMismatch(Generic[GenerationT, AccessT]):
    """Generation matches, but the requested release target does not."""

    generation: GenerationT
    current_access: AccessT

    def __post_init__(self) -> None:
        if self.generation is None:
            raise TypeError("generation cannot be None")
        if self.current_access is None:
            raise TypeError("current_access cannot be None")


@dataclass(frozen=True, slots=True)
class ReleaseInactive(Generic[GenerationT]):
    """No current access; a revoked acquisition may still be draining."""

    generation: GenerationT

    def __post_init__(self) -> None:
        if self.generation is None:
            raise TypeError("generation cannot be None")


@dataclass(frozen=True, slots=True)
class ReleaseAcquisitionRevoked(Generic[GenerationT, AccessT]):
    """Matching in-flight acquisition lost commit authority and is now draining."""

    generation: GenerationT
    access: AccessT
    next_generation: GenerationT

    def __post_init__(self) -> None:
        if self.generation is None:
            raise TypeError("generation cannot be None")
        if self.access is None:
            raise TypeError("access cannot be None")
        if self.next_generation is None:
            raise TypeError("next_generation cannot be None")
        if self.next_generation == self.generation:
            raise ValueError("next_generation must differ from generation")


@dataclass(frozen=True, slots=True)
class ReleaseAccessDetached(Generic[GenerationT, AccessT]):
    """Matching access was detached from the current lifecycle state."""

    generation: GenerationT
    access: AccessT
    next_generation: GenerationT

    def __post_init__(self) -> None:
        if self.generation is None:
            raise TypeError("generation cannot be None")
        if self.access is None:
            raise TypeError("access cannot be None")
        if self.next_generation is None:
            raise TypeError("next_generation cannot be None")
        if self.next_generation == self.generation:
            raise ValueError("next_generation must differ from generation")


ReleaseResult: TypeAlias = (
    ReleaseGenerationMismatch[GenerationT, AccessT]
    | ReleaseAccessMismatch[GenerationT, AccessT]
    | ReleaseInactive[GenerationT]
    | ReleaseAcquisitionRevoked[GenerationT, AccessT]
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
    "AcquireGenerationMismatch",
    "AcquireStartBlocked",
    "AcquireStartBusy",
    "AcquireStartExisting",
    "AcquireStartResult",
    "AcquireSuperseded",
    "CleanupRegistrationError",
    "ReleaseAccessDetached",
    "ReleaseAccessMismatch",
    "ReleaseAcquisitionRevoked",
    "ReleaseGenerationMismatch",
    "ReleaseInactive",
    "ReleaseResult",
]
