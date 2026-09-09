from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Generic, TypeAlias, TypeVar


GenerationT = TypeVar("GenerationT")
ResourceT = TypeVar("ResourceT")


@dataclass(frozen=True, slots=True, eq=False)
class AcquireToken:
    """Opaque identity token for one in-flight acquisition attempt.

    The token deliberately carries no generation, cancellation, timing, or commit-authority
    state. Those facts belong to the lifecycle state machine and the ``AcquireStarted`` result. Token
    identity is
    used only to correlate a returning acquisition with the state that started it.
    """


@dataclass(frozen=True, slots=True)
class AcquireStarted(Generic[GenerationT]):
    """Facts captured when an idle lifecycle state starts one acquisition attempt."""

    token: AcquireToken
    generation: GenerationT
    cancellation: Event


@dataclass(frozen=True, slots=True)
class AcquireExisting(Generic[ResourceT]):
    """An acquisition cannot start because a current usable resource already exists."""

    resource: ResourceT


@dataclass(frozen=True, slots=True)
class AcquireBusy:
    """An acquisition cannot start because another acquisition is still in flight."""

    draining: bool = False


@dataclass(frozen=True, slots=True)
class AcquireBlocked:
    """An acquisition cannot start because a domain precondition currently blocks it."""

    diagnostic: str | None = None


AcquireStartResult: TypeAlias = (
    AcquireStarted[GenerationT]
    | AcquireExisting[ResourceT]
    | AcquireBusy
    | AcquireBlocked
)


@dataclass(frozen=True, slots=True)
class ReleaseGenerationMismatch(Generic[GenerationT, ResourceT]):
    current_generation: GenerationT
    resource: ResourceT | None


@dataclass(frozen=True, slots=True)
class ReleaseInactive(Generic[GenerationT]):
    """No current resource; a revoked acquisition may still be draining."""

    generation: GenerationT


@dataclass(frozen=True, slots=True)
class ReleaseAcquisitionRevoked(Generic[GenerationT]):
    """Matching in-flight acquisition lost commit authority and is now draining."""

    generation: GenerationT


@dataclass(frozen=True, slots=True)
class ReleaseResourceDetached(Generic[GenerationT, ResourceT]):
    """Matching resource was detached from the current lifecycle state."""

    generation: GenerationT
    resource: ResourceT


ReleaseResult: TypeAlias = (
    ReleaseGenerationMismatch[GenerationT, ResourceT]
    | ReleaseInactive[GenerationT]
    | ReleaseAcquisitionRevoked[GenerationT]
    | ReleaseResourceDetached[GenerationT, ResourceT]
)


class CleanupRegistrationError(RuntimeError):
    """Cleanup registration failed after the lifecycle transition was already applied.

    ``outcome`` records the applied resource-detach result even if another thread has since changed
    lifecycle state. The failed registration remains retained by the state machine and is retried
    before another acquisition can begin.
    """

    def __init__(self, outcome: ReleaseResourceDetached) -> None:
        self.outcome = outcome
        super().__init__("lifecycle transition applied, but cleanup registration failed")


__all__ = [
    "AcquireBlocked",
    "AcquireBusy",
    "AcquireExisting",
    "AcquireStarted",
    "AcquireStartResult",
    "AcquireToken",
    "CleanupRegistrationError",
    "ReleaseAcquisitionRevoked",
    "ReleaseGenerationMismatch",
    "ReleaseInactive",
    "ReleaseResourceDetached",
    "ReleaseResult",
]
