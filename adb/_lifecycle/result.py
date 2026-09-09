from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Generic, TypeAlias, TypeVar

from adb._lifecycle.resource import ResourceScope


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")


@dataclass(frozen=True, slots=True, eq=False)
class AcquireAttempt(Generic[GenerationT]):
    """One in-flight acquisition attempt and its captured lifecycle context.

    Attempt identity correlates returning acquisition work with the state that started it.
    Generation fencing and commit authority remain state-machine concerns; the captured generation,
    cancellation, and resource scope are the context for this specific attempt.
    """

    generation: GenerationT
    cancellation: Event
    resource_scope: ResourceScope


@dataclass(frozen=True, slots=True)
class AcquireExisting(Generic[AccessT]):
    """An acquisition cannot start because a current usable access already exists."""

    access: AccessT


@dataclass(frozen=True, slots=True)
class AcquireBusy:
    """An acquisition cannot start because another acquisition is still in flight."""

    draining: bool = False


@dataclass(frozen=True, slots=True)
class AcquireBlocked:
    """An acquisition cannot start because a domain precondition currently blocks it."""

    diagnostic: str | None = None


AcquireStartResult: TypeAlias = (
    AcquireAttempt[GenerationT]
    | AcquireExisting[AccessT]
    | AcquireBusy
    | AcquireBlocked
)


@dataclass(frozen=True, slots=True)
class ReleaseGenerationMismatch(Generic[GenerationT, AccessT]):
    current_generation: GenerationT
    access: AccessT | None


@dataclass(frozen=True, slots=True)
class ReleaseInactive(Generic[GenerationT]):
    """No current access; a revoked acquisition may still be draining."""

    generation: GenerationT


@dataclass(frozen=True, slots=True)
class ReleaseAcquisitionRevoked(Generic[GenerationT]):
    """Matching in-flight acquisition lost commit authority and is now draining."""

    generation: GenerationT


@dataclass(frozen=True, slots=True)
class ReleaseAccessDetached(Generic[GenerationT, AccessT]):
    """Matching access was detached from the current lifecycle state."""

    generation: GenerationT
    access: AccessT


ReleaseResult: TypeAlias = (
    ReleaseGenerationMismatch[GenerationT, AccessT]
    | ReleaseInactive[GenerationT]
    | ReleaseAcquisitionRevoked[GenerationT]
    | ReleaseAccessDetached[GenerationT, AccessT]
)


class CleanupRegistrationError(RuntimeError):
    """Cleanup registration failed after the lifecycle transition was already applied.

    ``outcome`` records the applied access-detach result even if another thread has since changed
    lifecycle state. The failed registration remains retained by the state machine and is retried
    before another acquisition can begin.
    """

    def __init__(self, outcome: ReleaseAccessDetached) -> None:
        self.outcome = outcome
        super().__init__("lifecycle transition applied, but cleanup registration failed")


__all__ = [
    "AcquireAttempt",
    "AcquireBlocked",
    "AcquireBusy",
    "AcquireExisting",
    "AcquireStartResult",
    "CleanupRegistrationError",
    "ReleaseAccessDetached",
    "ReleaseAcquisitionRevoked",
    "ReleaseGenerationMismatch",
    "ReleaseInactive",
    "ReleaseResult",
]
