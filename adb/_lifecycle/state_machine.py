from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event, Lock
from time import monotonic
from typing import Generic, TypeAlias, TypeVar

from adb._lifecycle.resource import ResourceScope
from adb._lifecycle.result import (
    AcquireAttempt,
    AcquireBlocked,
    AcquireBusy,
    AcquireExisting,
    AcquireStartResult,
    CleanupRegistrationError,
    ReleaseAcquisitionRevoked,
    ReleaseGenerationMismatch,
    ReleaseInactive,
    ReleaseAccessDetached,
    ReleaseResult,
)


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")


@dataclass(frozen=True, slots=True)
class _Idle(Generic[GenerationT]):
    """Current generation has no acquisition work or usable access."""

    generation: GenerationT


@dataclass(frozen=True, slots=True)
class _Acquiring(Generic[GenerationT]):
    """Current generation has one in-flight acquisition with commit authority."""

    attempt: AcquireAttempt[GenerationT]
    started_at: float = field(default_factory=monotonic, repr=False, compare=False)

    @property
    def generation(self) -> GenerationT:
        return self.attempt.generation


@dataclass(frozen=True, slots=True)
class _Current(Generic[GenerationT, AccessT]):
    """Current generation retains one usable access."""

    generation: GenerationT
    access: AccessT
    resource_scope: ResourceScope


@dataclass(frozen=True, slots=True)
class _Draining(Generic[GenerationT]):
    """A revoked acquisition is draining after the state machine advanced to a new generation.

    ``generation`` is the current generation exposed by the state machine. ``attempt.generation`` is
    the generation of the in-flight attempt that has already lost commit authority but has not yet
    returned to the state machine.
    """

    generation: GenerationT
    attempt: AcquireAttempt[GenerationT]
    started_at: float = field(repr=False, compare=False)


_State: TypeAlias = (
    _Idle[GenerationT]
    | _Acquiring[GenerationT]
    | _Current[GenerationT, AccessT]
    | _Draining[GenerationT]
)


@dataclass(frozen=True, slots=True)
class PendingSnapshot(Generic[GenerationT]):
    generation: GenerationT
    cancelled: bool
    age_seconds: float


@dataclass(frozen=True, slots=True)
class LifecycleSnapshot(Generic[GenerationT, AccessT]):
    """Atomic lifecycle snapshot for domain-facing state projection."""

    generation: GenerationT
    access: AccessT | None
    pending: PendingSnapshot[GenerationT] | None = None
    cleanup_registration_errors: tuple[str, ...] = ()


@dataclass(slots=True)
class _FailedCleanupRegistration:
    callback: Callable[[], None]
    diagnostic: str


class LifecycleStateMachine(Generic[GenerationT, AccessT]):
    """Shared acquire/commit/release lifecycle state machine.

    Lifecycle state is represented as one explicit private state: ``_Idle``, ``_Acquiring``,
    ``_Current``, or ``_Draining``. This makes in-flight acquisition and a current usable access
    mutually exclusive by construction. An ``AcquireAttempt`` is identity-bearing and captures the
    context for one in-flight acquisition; generation fencing is expressed by state transitions,
    especially ``_Acquiring(A1@G1) -> _Draining(G2, A1@G1)``.

    Every in-flight acquisition owns a ``ResourceScope`` that follows it through revocation,
    commit, or abandonment. Domain adapters populate that scope as physical resources are obtained;
    the state machine keeps the committed value and its scope associated in ``_Current``. Domain
    lifecycles retain responsibility for acquisition constraints, cleanup implementation, claim
    semantics, domain values, and notifications. This state machine owns the concurrency-sensitive
    authority/ownership relationship and invokes narrow callbacks while holding its state lock when
    cleanup-debt registration must stay atomic with a state transition.

    All callbacks (including the issuer) must be short, non-reentrant, and perform no I/O, thread
    startup, or waits for external work. Nested state locks must follow a consistent lock order.
    Cleanup-registration callbacks must retain supporting resources in their closure and be
    idempotent: a failed registration is retained and retried before another acquisition can begin.
    Physical cleanup and notifications belong outside this state machine's lock.

    The issuer must never reuse a generation within this state-machine scope. The adjacent-value
    check is a defensive check, not a replacement for that contract (it cannot detect ABA reuse).
    """

    def __init__(self, issue_generation: Callable[[], GenerationT]) -> None:
        if not callable(issue_generation):
            raise TypeError("issue_generation must be callable")
        self._issue_generation = issue_generation
        self._lock = Lock()
        self._state: _State[GenerationT, AccessT] = _Idle(issue_generation())
        self._failed_cleanup_registrations: list[_FailedCleanupRegistration] = []

    def _register_cleanup_locked(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except BaseException as exc:
            # Retain the closure/resource and block acquisition if registration failed before the
            # domain could record its cleanup debt. Clearing in-flight state alone would be unsafe.
            self._failed_cleanup_registrations.append(
                _FailedCleanupRegistration(callback, str(exc))
            )
            raise

    def _retry_cleanup_registrations_locked(self) -> bool:
        while self._failed_cleanup_registrations:
            registration = self._failed_cleanup_registrations[0]
            try:
                registration.callback()
            except Exception as exc:
                registration.diagnostic = str(exc)
                return False
            self._failed_cleanup_registrations.pop(0)
        return True

    @staticmethod
    def _pending_snapshot(
        state: _Acquiring[GenerationT] | _Draining[GenerationT],
    ) -> PendingSnapshot[GenerationT]:
        return PendingSnapshot(
            generation=state.attempt.generation,
            cancelled=(
                isinstance(state, _Draining) or state.attempt.cancellation.is_set()
            ),
            age_seconds=max(0.0, monotonic() - state.started_at),
        )

    def snapshot(self) -> LifecycleSnapshot[GenerationT, AccessT]:
        with self._lock:
            state = self._state
            access = state.access if isinstance(state, _Current) else None
            pending = (
                self._pending_snapshot(state)
                if isinstance(state, (_Acquiring, _Draining))
                else None
            )
            return LifecycleSnapshot(
                generation=state.generation,
                access=access,
                pending=pending,
                cleanup_registration_errors=tuple(
                    item.diagnostic for item in self._failed_cleanup_registrations
                ),
            )

    def begin_acquire(
        self,
        *,
        is_blocked: Callable[[], bool] | None = None,
    ) -> AcquireStartResult[GenerationT, AccessT]:
        """Atomically inspect lifecycle state and, when allowed, start one acquisition attempt."""

        if is_blocked is not None and not callable(is_blocked):
            raise TypeError("is_blocked must be callable or None")

        with self._lock:
            state = self._state
            if isinstance(state, _Current):
                return AcquireExisting(state.access)
            if isinstance(state, _Acquiring):
                return AcquireBusy(draining=False)
            if isinstance(state, _Draining):
                return AcquireBusy(draining=True)
            if not isinstance(state, _Idle):
                raise RuntimeError("unsupported lifecycle state")

            if not self._retry_cleanup_registrations_locked():
                return AcquireBlocked(
                    "lifecycle cleanup registration failed; retry required"
                )
            if is_blocked is not None and is_blocked():
                return AcquireBlocked()

            attempt = AcquireAttempt(
                generation=state.generation,
                cancellation=Event(),
                resource_scope=ResourceScope(),
            )
            self._state = _Acquiring(attempt=attempt)
            return attempt

    def abandon_acquire(
        self,
        attempt: AcquireAttempt[GenerationT],
        *,
        before_clear: Callable[[], None] | None = None,
    ) -> bool:
        """Finish a failed/abandoned acquisition and report whether it was already revoked.

        A failing ``before_clear`` still retires the matching in-flight state. Its
        resource-retaining cleanup registration remains queued for retry and blocks new acquisitions
        until registration succeeds.
        """

        if not isinstance(attempt, AcquireAttempt):
            raise TypeError("attempt must be AcquireAttempt")
        if before_clear is not None and not callable(before_clear):
            raise TypeError("before_clear must be callable or None")

        with self._lock:
            state = self._state
            matches_acquiring = isinstance(state, _Acquiring) and state.attempt is attempt
            matches_draining = isinstance(state, _Draining) and state.attempt is attempt
            if not matches_acquiring and not matches_draining:
                raise RuntimeError("acquisition attempt is not current")

            revoked = matches_draining
            attempt.resource_scope.seal()
            try:
                if before_clear is not None:
                    self._register_cleanup_locked(before_clear)
            finally:
                self._state = _Idle(state.generation)
            return revoked

    def commit_acquire(
        self,
        attempt: AcquireAttempt[GenerationT],
        access: AccessT,
        *,
        on_superseded: Callable[[], None] | None = None,
    ) -> bool:
        """Commit ``access`` iff ``attempt`` still has authority; otherwise register cleanup."""

        if not isinstance(attempt, AcquireAttempt):
            raise TypeError("attempt must be AcquireAttempt")
        if access is None:
            raise ValueError("access cannot be None")
        if on_superseded is not None and not callable(on_superseded):
            raise TypeError("on_superseded must be callable or None")

        with self._lock:
            state = self._state
            if isinstance(state, (_Acquiring, _Draining)) and state.attempt is attempt:
                attempt.resource_scope.seal()
            if isinstance(state, _Acquiring) and state.attempt is attempt:
                self._state = _Current(state.generation, access, attempt.resource_scope)
                return True

            try:
                if on_superseded is not None:
                    self._register_cleanup_locked(on_superseded)
            finally:
                if isinstance(state, _Draining) and state.attempt is attempt:
                    self._state = _Idle(state.generation)
            return False

    def release(
        self,
        expected: GenerationT,
        *,
        on_access_release: Callable[[AccessT, ResourceScope], None] | None = None,
        inconsistent_state_error: str = "lifecycle state is inconsistent",
    ) -> ReleaseResult[GenerationT, AccessT]:
        """Release matching lifecycle state, advance generation, and fence stale acquisition work."""

        if on_access_release is not None and not callable(on_access_release):
            raise TypeError("on_access_release must be callable or None")
        if not isinstance(inconsistent_state_error, str):
            raise TypeError("inconsistent_state_error must be a string")
        normalized_error = inconsistent_state_error.strip()
        if not normalized_error:
            raise ValueError("inconsistent_state_error cannot be empty")

        with self._lock:
            state = self._state
            if expected != state.generation:
                return ReleaseGenerationMismatch(
                    current_generation=state.generation,
                    access=state.access if isinstance(state, _Current) else None,
                )

            if isinstance(state, (_Idle, _Draining)):
                return ReleaseInactive(expected)

            released_generation = state.generation
            next_generation = self._issue_generation()
            if next_generation == released_generation:
                raise RuntimeError("issue_generation must return a fresh generation")

            if isinstance(state, _Acquiring):
                # Logical revocation is immediate, but the old attempt remains represented until
                # obtain returns. New acquisition is therefore blocked without comparing
                # generations.
                state.attempt.cancellation.set()
                self._state = _Draining(
                    generation=next_generation,
                    attempt=state.attempt,
                    started_at=state.started_at,
                )
                return ReleaseAcquisitionRevoked(released_generation)

            if not isinstance(state, _Current):
                raise RuntimeError(normalized_error)

            access = state.access
            self._state = _Idle(next_generation)
            outcome = ReleaseAccessDetached(released_generation, access)
            if on_access_release is not None:
                try:
                    self._register_cleanup_locked(
                        lambda: on_access_release(access, state.resource_scope)
                    )
                except Exception as exc:
                    raise CleanupRegistrationError(outcome) from exc
            return outcome


__all__ = [
    "LifecycleSnapshot",
    "LifecycleStateMachine",
    "PendingSnapshot",
]
