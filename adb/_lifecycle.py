from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event, Lock
from time import monotonic
from typing import Generic, TypeAlias, TypeVar


GenerationT = TypeVar("GenerationT")
OwnershipT = TypeVar("OwnershipT")


@dataclass(frozen=True, slots=True, eq=False)
class LifecyclePendingAcquire:
    """Opaque identity token for one in-flight acquisition attempt.

    The token deliberately carries no generation, cancellation, timing, or authority state. Those
    facts belong to the lifecycle state machine and the ``LifecycleAcquireStarted`` transition
    result. Token identity is used only to correlate a returning acquisition with the state that
    started it.
    """


@dataclass(frozen=True, slots=True)
class LifecycleAcquireStarted(Generic[GenerationT]):
    """Facts captured when an idle lifecycle starts one acquisition attempt."""

    pending: LifecyclePendingAcquire
    generation: GenerationT
    cancellation: Event


@dataclass(frozen=True, slots=True)
class LifecycleIdle(Generic[GenerationT]):
    """Current generation has no acquisition work or usable ownership."""

    generation: GenerationT


@dataclass(frozen=True, slots=True)
class LifecycleAcquiring(Generic[GenerationT]):
    """Current generation has one in-flight acquisition with commit authority."""

    generation: GenerationT
    pending: LifecyclePendingAcquire
    cancellation: Event
    started_at: float = field(default_factory=monotonic, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class LifecycleAcquired(Generic[GenerationT, OwnershipT]):
    """Current generation owns one usable resource."""

    generation: GenerationT
    ownership: OwnershipT


@dataclass(frozen=True, slots=True)
class LifecycleCancelling(Generic[GenerationT]):
    """A revoked acquisition is draining after authority advanced to a new generation.

    ``generation`` is the current generation exposed by the lifecycle. ``cancelled_generation`` is
    the generation of the in-flight attempt that has already lost commit authority but has not yet
    returned to the core.
    """

    generation: GenerationT
    cancelled_generation: GenerationT
    pending: LifecyclePendingAcquire
    cancellation: Event
    started_at: float = field(repr=False, compare=False)


LifecycleState: TypeAlias = (
    LifecycleIdle[GenerationT]
    | LifecycleAcquiring[GenerationT]
    | LifecycleAcquired[GenerationT, OwnershipT]
    | LifecycleCancelling[GenerationT]
)


@dataclass(frozen=True, slots=True)
class LifecycleAcquireOwned(Generic[OwnershipT]):
    """An acquisition cannot start because usable ownership already exists."""

    ownership: OwnershipT


@dataclass(frozen=True, slots=True)
class LifecycleAcquireBusy:
    """An acquisition cannot start because another acquisition is still in flight."""

    draining: bool = False


@dataclass(frozen=True, slots=True)
class LifecycleAcquireBlocked:
    """An acquisition cannot start because a domain precondition currently blocks it."""

    diagnostic: str | None = None


@dataclass(frozen=True, slots=True)
class LifecyclePendingSnapshot(Generic[GenerationT]):
    generation: GenerationT
    cancelled: bool
    age_seconds: float


@dataclass(frozen=True, slots=True)
class LifecycleAuthoritySnapshot(Generic[GenerationT, OwnershipT]):
    """Atomic lifecycle authority snapshot for domain-facing state projection."""

    generation: GenerationT
    ownership: OwnershipT | None
    pending: LifecyclePendingSnapshot[GenerationT] | None = None
    retirement_errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LifecycleDiagnostics(Generic[GenerationT]):
    """Diagnostic samples of authority and cleanup, taken under their respective locks.

    These samples do not form a transaction across both components and are not acquire permission.
    """

    generation: GenerationT
    pending: LifecyclePendingSnapshot[GenerationT] | None
    retirement_errors: tuple[str, ...]
    cleanup_pending_count: int
    cleanup_handoff_accepted_count: int
    cleanup_handoff_errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LifecycleReleaseGenerationMismatch(Generic[GenerationT, OwnershipT]):
    current_generation: GenerationT
    ownership: OwnershipT | None


@dataclass(frozen=True, slots=True)
class LifecycleReleaseInactive(Generic[GenerationT]):
    """No current authority; a revoked acquisition may still be draining."""

    generation: GenerationT


@dataclass(frozen=True, slots=True)
class LifecycleReleasePending(Generic[GenerationT]):
    generation: GenerationT


@dataclass(frozen=True, slots=True)
class LifecycleReleaseOwned(Generic[GenerationT, OwnershipT]):
    generation: GenerationT
    ownership: OwnershipT


class LifecycleTransitionCallbackError(RuntimeError):
    """A release callback failed AFTER the authority transition was applied.

    ``outcome`` records that applied transition even if another thread has since changed state.
    A failed release handoff is retained by the core and retried on the next acquisition attempt.
    """

    def __init__(self, outcome: LifecycleReleaseOwned) -> None:
        self.outcome = outcome
        super().__init__("lifecycle transition applied, but its locked callback failed")


@dataclass(slots=True)
class _FailedRetirement:
    callback: Callable[[], None]
    diagnostic: str


class LifecycleAuthorityCore(Generic[GenerationT, OwnershipT]):
    """Shared lifecycle authority state machine.

    Authority is represented as one explicit ADT state: ``Idle``, ``Acquiring``, ``Acquired``, or
    ``Cancelling``. This makes pending acquisition and usable ownership mutually exclusive by
    construction. A pending token is identity-only; generation fencing is expressed by state
    transitions, especially ``Acquiring(G1) -> Cancelling(G2, cancelled_generation=G1)``.

    Domain lifecycles retain responsibility for validating acquisition constraints, obtaining and
    cleaning physical resources, constructing domain acquisition values, and publishing domain
    notifications. This core owns only the concurrency-sensitive authority state and invokes narrow
    callbacks while holding its state lock when a domain effect must stay atomic with that state.

    All callbacks (including the issuer) must be short, non-reentrant, and perform no I/O, thread
    startup, or waits for external work. Nested state locks must follow a consistent lock order.
    Commit callbacks are state projections, not notifications, and must not raise. Retirement
    callbacks only register cleanup debt, must retain the resource in their closure, and must be
    idempotent: a failed handoff is retained and retried before another acquisition can begin.
    Physical cleanup and notifications belong outside this core's lock.

    The issuer must never reuse a generation within this authority scope. The adjacent-value
    check is a defensive check, not a replacement for that contract (it cannot detect ABA reuse).
    """

    def __init__(self, issue_generation: Callable[[], GenerationT]) -> None:
        if not callable(issue_generation):
            raise TypeError("issue_generation must be callable")
        self._issue_generation = issue_generation
        self._lock = Lock()
        self._state: LifecycleState[GenerationT, OwnershipT] = LifecycleIdle(
            issue_generation()
        )
        self._failed_retirements: list[_FailedRetirement] = []

    def _retire_locked(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except BaseException as exc:
            # Retain the closure/resource and block acquisition if handoff failed before the
            # domain could record its cleanup debt. Clearing in-flight state alone would be unsafe.
            self._failed_retirements.append(_FailedRetirement(callback, str(exc)))
            raise

    def _retry_retirements_locked(self) -> bool:
        while self._failed_retirements:
            retirement = self._failed_retirements[0]
            try:
                retirement.callback()
            except Exception as exc:
                retirement.diagnostic = str(exc)
                return False
            self._failed_retirements.pop(0)
        return True

    @staticmethod
    def _pending_snapshot(
        state: LifecycleAcquiring[GenerationT] | LifecycleCancelling[GenerationT],
    ) -> LifecyclePendingSnapshot[GenerationT]:
        generation = (
            state.cancelled_generation
            if isinstance(state, LifecycleCancelling)
            else state.generation
        )
        return LifecyclePendingSnapshot(
            generation=generation,
            cancelled=isinstance(state, LifecycleCancelling) or state.cancellation.is_set(),
            age_seconds=max(0.0, monotonic() - state.started_at),
        )

    def snapshot(self) -> LifecycleAuthoritySnapshot[GenerationT, OwnershipT]:
        with self._lock:
            state = self._state
            ownership = state.ownership if isinstance(state, LifecycleAcquired) else None
            pending = (
                self._pending_snapshot(state)
                if isinstance(state, (LifecycleAcquiring, LifecycleCancelling))
                else None
            )
            return LifecycleAuthoritySnapshot(
                generation=state.generation,
                ownership=ownership,
                pending=pending,
                retirement_errors=tuple(item.diagnostic for item in self._failed_retirements),
            )

    def begin_acquire(
        self,
        *,
        is_blocked: Callable[[], bool] | None = None,
    ) -> (
        LifecycleAcquireOwned[OwnershipT]
        | LifecycleAcquireBusy
        | LifecycleAcquireBlocked
        | LifecycleAcquireStarted[GenerationT]
    ):
        """Atomically inspect authority and, when allowed, start one acquisition attempt."""

        if is_blocked is not None and not callable(is_blocked):
            raise TypeError("is_blocked must be callable or None")

        with self._lock:
            state = self._state
            if isinstance(state, LifecycleAcquired):
                return LifecycleAcquireOwned(state.ownership)
            if isinstance(state, LifecycleAcquiring):
                return LifecycleAcquireBusy(draining=False)
            if isinstance(state, LifecycleCancelling):
                return LifecycleAcquireBusy(draining=True)
            if not isinstance(state, LifecycleIdle):
                raise RuntimeError("unsupported lifecycle authority state")

            if not self._retry_retirements_locked():
                return LifecycleAcquireBlocked("lifecycle cleanup handoff failed; retry required")
            if is_blocked is not None and is_blocked():
                return LifecycleAcquireBlocked()

            pending = LifecyclePendingAcquire()
            cancellation = Event()
            acquiring = LifecycleAcquiring(
                generation=state.generation,
                pending=pending,
                cancellation=cancellation,
            )
            self._state = acquiring
            return LifecycleAcquireStarted(
                pending=pending,
                generation=acquiring.generation,
                cancellation=cancellation,
            )

    def abandon_acquire(
        self,
        pending: LifecyclePendingAcquire,
        *,
        before_clear: Callable[[], None] | None = None,
    ) -> bool:
        """Finish a failed/abandoned acquisition and report whether it was already revoked.

        A failing ``before_clear`` still retires the matching in-flight state. Its resource-retaining
        callback remains queued for retry and blocks new acquisitions until handoff succeeds.
        """

        if not isinstance(pending, LifecyclePendingAcquire):
            raise TypeError("pending must be LifecyclePendingAcquire")
        if before_clear is not None and not callable(before_clear):
            raise TypeError("before_clear must be callable or None")

        with self._lock:
            state = self._state
            matches_acquiring = (
                isinstance(state, LifecycleAcquiring) and state.pending is pending
            )
            matches_cancelling = (
                isinstance(state, LifecycleCancelling) and state.pending is pending
            )
            if not matches_acquiring and not matches_cancelling:
                raise RuntimeError("pending acquisition token is not current")

            revoked = matches_cancelling
            try:
                if before_clear is not None:
                    self._retire_locked(before_clear)
            finally:
                self._state = LifecycleIdle(state.generation)
            return revoked

    def commit_acquire(
        self,
        pending: LifecyclePendingAcquire,
        ownership: OwnershipT,
        *,
        on_superseded: Callable[[], None] | None = None,
    ) -> bool:
        """Commit ownership iff ``pending`` still has authority; otherwise retire the resource."""

        if not isinstance(pending, LifecyclePendingAcquire):
            raise TypeError("pending must be LifecyclePendingAcquire")
        if ownership is None:
            raise ValueError("ownership cannot be None")
        if on_superseded is not None and not callable(on_superseded):
            raise TypeError("on_superseded must be callable or None")

        with self._lock:
            state = self._state
            if isinstance(state, LifecycleAcquiring) and state.pending is pending:
                self._state = LifecycleAcquired(state.generation, ownership)
                return True

            try:
                if on_superseded is not None:
                    self._retire_locked(on_superseded)
            finally:
                if isinstance(state, LifecycleCancelling) and state.pending is pending:
                    self._state = LifecycleIdle(state.generation)
            return False

    def release(
        self,
        expected: GenerationT,
        *,
        on_owned_release: Callable[[OwnershipT], None] | None = None,
        inconsistent_state_error: str = "lifecycle authority state is inconsistent",
    ) -> (
        LifecycleReleaseGenerationMismatch[GenerationT, OwnershipT]
        | LifecycleReleaseInactive[GenerationT]
        | LifecycleReleasePending[GenerationT]
        | LifecycleReleaseOwned[GenerationT, OwnershipT]
    ):
        """Release matching authority, advance generation, and fence stale acquisition work."""

        if on_owned_release is not None and not callable(on_owned_release):
            raise TypeError("on_owned_release must be callable or None")
        if not isinstance(inconsistent_state_error, str):
            raise TypeError("inconsistent_state_error must be a string")
        normalized_error = inconsistent_state_error.strip()
        if not normalized_error:
            raise ValueError("inconsistent_state_error cannot be empty")

        with self._lock:
            state = self._state
            if expected != state.generation:
                return LifecycleReleaseGenerationMismatch(
                    current_generation=state.generation,
                    ownership=(
                        state.ownership if isinstance(state, LifecycleAcquired) else None
                    ),
                )

            if isinstance(state, (LifecycleIdle, LifecycleCancelling)):
                return LifecycleReleaseInactive(expected)

            released_generation = state.generation
            next_generation = self._issue_generation()
            if next_generation == released_generation:
                raise RuntimeError("issue_generation must return a fresh generation")

            if isinstance(state, LifecycleAcquiring):
                # Logical revocation is immediate, but the old attempt remains represented until
                # obtain returns. New acquisition is therefore blocked without comparing generations.
                state.cancellation.set()
                self._state = LifecycleCancelling(
                    generation=next_generation,
                    cancelled_generation=released_generation,
                    pending=state.pending,
                    cancellation=state.cancellation,
                    started_at=state.started_at,
                )
                return LifecycleReleasePending(released_generation)

            if not isinstance(state, LifecycleAcquired):
                raise RuntimeError(normalized_error)

            ownership = state.ownership
            self._state = LifecycleIdle(next_generation)
            outcome = LifecycleReleaseOwned(released_generation, ownership)
            if on_owned_release is not None:
                try:
                    self._retire_locked(lambda: on_owned_release(ownership))
                except Exception as exc:
                    raise LifecycleTransitionCallbackError(outcome) from exc
            return outcome


__all__ = [
    "LifecycleAcquireBlocked",
    "LifecycleAcquireBusy",
    "LifecycleAcquireOwned",
    "LifecycleAcquireStarted",
    "LifecycleAcquired",
    "LifecycleAcquiring",
    "LifecycleAuthorityCore",
    "LifecycleAuthoritySnapshot",
    "LifecycleCancelling",
    "LifecycleDiagnostics",
    "LifecycleIdle",
    "LifecyclePendingAcquire",
    "LifecyclePendingSnapshot",
    "LifecycleReleaseGenerationMismatch",
    "LifecycleReleaseInactive",
    "LifecycleReleaseOwned",
    "LifecycleReleasePending",
    "LifecycleState",
    "LifecycleTransitionCallbackError",
]
