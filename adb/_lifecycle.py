from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event, Lock
from time import monotonic
from typing import Generic, TypeVar


GenerationT = TypeVar("GenerationT")
OwnershipT = TypeVar("OwnershipT")


@dataclass(frozen=True, slots=True)
class LifecyclePendingAcquire(Generic[GenerationT]):
    """One in-flight acquisition fenced by the generation captured at its start."""

    generation: GenerationT
    cancellation: Event
    started_at: float = field(default_factory=monotonic, repr=False, compare=False)


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
    """A callback failed AFTER an authority transition was applied.

    ``outcome`` records that applied transition even if another thread has since changed state.
    A commit failure does not return resource ownership to the caller: release it through the core.
    A failed release handoff is retained by the core and retried on the next acquisition attempt.
    """

    def __init__(self, outcome: LifecycleAcquireOwned | LifecycleReleaseOwned) -> None:
        self.outcome = outcome
        super().__init__("lifecycle transition applied, but its locked callback failed")


@dataclass(slots=True)
class _FailedRetirement:
    callback: Callable[[], None]
    diagnostic: str


class LifecycleAuthorityCore(Generic[GenerationT, OwnershipT]):
    """Shared generation, pending-acquire, ownership, and release state machine.

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
        self._generation = issue_generation()
        self._pending: LifecyclePendingAcquire[GenerationT] | None = None
        self._ownership: OwnershipT | None = None
        self._failed_retirements: list[_FailedRetirement] = []

    def _check_invariants_locked(
        self, message: str = "pending and ownership cannot coexist"
    ) -> None:
        if self._pending is not None and self._ownership is not None:
            raise RuntimeError(message)

    def _retire_locked(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except BaseException as exc:
            # Retain the closure/resource and block acquisition if handoff failed before the
            # domain could record its cleanup debt. Clearing pending alone would be unsafe.
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

    def snapshot(self) -> LifecycleAuthoritySnapshot[GenerationT, OwnershipT]:
        with self._lock:
            self._check_invariants_locked()
            pending = self._pending
            return LifecycleAuthoritySnapshot(
                self._generation,
                self._ownership,
                pending=(
                    None if pending is None else LifecyclePendingSnapshot(
                        pending.generation,
                        pending.cancellation.is_set(),
                        max(0.0, monotonic() - pending.started_at),
                    )
                ),
                retirement_errors=tuple(item.diagnostic for item in self._failed_retirements),
            )

    def run_if_no_pending(self, action: Callable[[], None]) -> bool:
        """Run ``action`` atomically only while no acquisition is in flight."""

        if not callable(action):
            raise TypeError("action must be callable")
        with self._lock:
            self._check_invariants_locked()
            if self._pending is not None:
                return False
            action()
            return True

    def begin_acquire(
        self,
        *,
        is_blocked: Callable[[], bool] | None = None,
    ) -> (
        LifecycleAcquireOwned[OwnershipT]
        | LifecycleAcquireBusy
        | LifecycleAcquireBlocked
        | LifecyclePendingAcquire[GenerationT]
    ):
        """Atomically inspect authority and, when allowed, install one pending acquisition."""

        if is_blocked is not None and not callable(is_blocked):
            raise TypeError("is_blocked must be callable or None")

        with self._lock:
            self._check_invariants_locked()
            ownership = self._ownership
            if ownership is not None:
                return LifecycleAcquireOwned(ownership)
            if self._pending is not None:
                return LifecycleAcquireBusy(self._pending.generation != self._generation)
            if not self._retry_retirements_locked():
                return LifecycleAcquireBlocked("lifecycle cleanup handoff failed; retry required")
            if is_blocked is not None and is_blocked():
                return LifecycleAcquireBlocked()

            pending = LifecyclePendingAcquire(
                generation=self._generation,
                cancellation=Event(),
            )
            self._pending = pending
            return pending

    def abandon_acquire(
        self,
        pending: LifecyclePendingAcquire[GenerationT],
        *,
        before_clear: Callable[[], None] | None = None,
    ) -> bool:
        """Clear a completed/failed pending acquisition and report whether it was revoked.

        A failing ``before_clear`` still clears this pending, but its resource-retaining callback
        remains queued for retry and blocks new acquisitions until handoff succeeds.
        """

        if not isinstance(pending, LifecyclePendingAcquire):
            raise TypeError("pending must be LifecyclePendingAcquire")
        if before_clear is not None and not callable(before_clear):
            raise TypeError("before_clear must be callable or None")

        with self._lock:
            self._check_invariants_locked()
            revoked = self._generation != pending.generation
            try:
                if before_clear is not None:
                    self._retire_locked(before_clear)
            finally:
                if self._pending is pending:
                    self._pending = None
            return revoked

    def commit_acquire(
        self,
        pending: LifecyclePendingAcquire[GenerationT],
        ownership: OwnershipT,
        *,
        on_commit: Callable[[], None] | None = None,
        on_superseded: Callable[[], None] | None = None,
    ) -> bool:
        """Commit ownership iff ``pending`` is current; otherwise retire the obtained
        resource.
        """

        if not isinstance(pending, LifecyclePendingAcquire):
            raise TypeError("pending must be LifecyclePendingAcquire")
        if ownership is None:
            raise ValueError("ownership cannot be None")
        if on_commit is not None and not callable(on_commit):
            raise TypeError("on_commit must be callable or None")
        if on_superseded is not None and not callable(on_superseded):
            raise TypeError("on_superseded must be callable or None")

        with self._lock:
            self._check_invariants_locked()
            if self._pending is pending and self._generation == pending.generation:
                self._pending = None
                self._ownership = ownership
                if on_commit is not None:
                    try:
                        on_commit()
                    except Exception as exc:
                        raise LifecycleTransitionCallbackError(
                            LifecycleAcquireOwned(ownership)
                        ) from exc
                return True

            try:
                if on_superseded is not None:
                    self._retire_locked(on_superseded)
            finally:
                if self._pending is pending:
                    self._pending = None
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
            self._check_invariants_locked(normalized_error)
            if expected != self._generation:
                return LifecycleReleaseGenerationMismatch(
                    current_generation=self._generation,
                    ownership=self._ownership,
                )

            pending = self._pending
            current_pending = (
                pending
                if pending is not None and pending.generation == self._generation
                else None
            )
            ownership = self._ownership
            if current_pending is None and ownership is None:
                return LifecycleReleaseInactive(expected)

            released_generation = self._generation
            next_generation = self._issue_generation()
            if next_generation == released_generation:
                raise RuntimeError("issue_generation must return a fresh generation")
            self._generation = next_generation

            if current_pending is not None:
                # Deliberately retain the pending object until the in-flight obtain returns. A new
                # acquire therefore remains blocked even though generation has already advanced.
                current_pending.cancellation.set()
                return LifecycleReleasePending(released_generation)

            # The inactive and pending cases above have returned; ownership is present here.
            assert ownership is not None
            self._ownership = None
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
    "LifecycleAuthorityCore",
    "LifecycleAuthoritySnapshot",
    "LifecycleDiagnostics",
    "LifecyclePendingAcquire",
    "LifecyclePendingSnapshot",
    "LifecycleReleaseGenerationMismatch",
    "LifecycleReleaseInactive",
    "LifecycleReleaseOwned",
    "LifecycleReleasePending",
    "LifecycleTransitionCallbackError",
]
