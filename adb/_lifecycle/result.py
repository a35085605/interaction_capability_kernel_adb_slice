from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Generic, TypeAlias, TypeVar


GenerationT = TypeVar("GenerationT")
OwnershipT = TypeVar("OwnershipT")


@dataclass(frozen=True, slots=True, eq=False)
class AcquireToken:
    """Opaque identity token for one in-flight acquisition attempt.

    The token deliberately carries no generation, cancellation, timing, or authority state. Those
    facts belong to the authority state machine and the ``AcquireStarted`` result. Token identity is
    used only to correlate a returning acquisition with the state that started it.
    """


@dataclass(frozen=True, slots=True)
class AcquireStarted(Generic[GenerationT]):
    """Facts captured when an idle authority starts one acquisition attempt."""

    token: AcquireToken
    generation: GenerationT
    cancellation: Event


@dataclass(frozen=True, slots=True)
class AcquireExisting(Generic[OwnershipT]):
    """An acquisition cannot start because usable ownership already exists."""

    ownership: OwnershipT


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
    | AcquireExisting[OwnershipT]
    | AcquireBusy
    | AcquireBlocked
)


@dataclass(frozen=True, slots=True)
class ReleaseGenerationMismatch(Generic[GenerationT, OwnershipT]):
    current_generation: GenerationT
    ownership: OwnershipT | None


@dataclass(frozen=True, slots=True)
class ReleaseInactive(Generic[GenerationT]):
    """No current authority; a revoked acquisition may still be draining."""

    generation: GenerationT


@dataclass(frozen=True, slots=True)
class ReleaseAcquisitionRevoked(Generic[GenerationT]):
    """Matching in-flight acquisition lost commit authority and is now draining."""

    generation: GenerationT


@dataclass(frozen=True, slots=True)
class ReleaseOwnershipDetached(Generic[GenerationT, OwnershipT]):
    """Matching ownership was detached from current authority."""

    generation: GenerationT
    ownership: OwnershipT


ReleaseResult: TypeAlias = (
    ReleaseGenerationMismatch[GenerationT, OwnershipT]
    | ReleaseInactive[GenerationT]
    | ReleaseAcquisitionRevoked[GenerationT]
    | ReleaseOwnershipDetached[GenerationT, OwnershipT]
)


class CleanupRegistrationError(RuntimeError):
    """Cleanup registration failed after the authority transition was already applied.

    ``outcome`` records the applied ownership-detach result even if another thread has since changed
    authority state. The failed registration remains retained by the authority and is retried before
    another acquisition can begin.
    """

    def __init__(self, outcome: ReleaseOwnershipDetached) -> None:
        self.outcome = outcome
        super().__init__("authority transition applied, but cleanup registration failed")


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
    "ReleaseOwnershipDetached",
    "ReleaseResult",
]
