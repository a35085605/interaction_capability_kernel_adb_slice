from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event, Lock
from time import monotonic
from typing import Generic, TypeAlias, TypeVar

from adb._lifecycle.resource import ResourceScope
from adb._lifecycle.snapshot import Snapshot
from adb._lifecycle.result import (
    AcquireAbandonResult,
    AcquireAttempt,
    AcquireAttemptAbandoned,
    AcquireAttemptCommitted,
    AcquireAttemptRevoked,
    AcquireCommitResult,
    AcquireStartBlocked,
    AcquireStartBusy,
    AcquireStartCurrent,
    AcquireStartResult,
    CleanupRegistrationError,
    GenerationMismatch,
    ReleaseAccessMismatch,
    ReleaseAcquisitionRevoked,
    ReleaseInactive,
    ReleaseAccessDetached,
    ReleaseResult,
)


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")
CapabilityT = TypeVar("CapabilityT")


@dataclass(frozen=True, slots=True)
class _Idle(Generic[GenerationT]):
    """Current generation has no acquisition work or usable access."""

    generation: GenerationT


@dataclass(frozen=True, slots=True)
class _Acquiring(Generic[GenerationT, AccessT]):
    """Current generation has one in-flight acquisition with commit authority."""

    attempt: AcquireAttempt[GenerationT, AccessT]
    started_at: float = field(default_factory=monotonic, repr=False, compare=False)

    @property
    def generation(self) -> GenerationT:
        return self.attempt.generation


@dataclass(frozen=True, slots=True)
class _Current(Generic[GenerationT, AccessT, CapabilityT]):
    """Current generation retains one usable access and operation capability."""

    generation: GenerationT
    access: AccessT
    capability: CapabilityT
    resource_scope: ResourceScope


@dataclass(frozen=True, slots=True)
class _Draining(Generic[GenerationT, AccessT]):
    """A revoked acquisition is draining after authority advanced to a new generation.

    ``generation`` is the current generation exposed by the state machine.
    ``attempt.generation`` and ``attempt.access`` identify the in-flight request that already lost
    commit authority.
    """

    generation: GenerationT
    attempt: AcquireAttempt[GenerationT, AccessT]
    started_at: float = field(repr=False, compare=False)


_State: TypeAlias = (
    _Idle[GenerationT]
    | _Acquiring[GenerationT, AccessT]
    | _Current[GenerationT, AccessT, CapabilityT]
    | _Draining[GenerationT, AccessT]
)


@dataclass(frozen=True, slots=True)
class PendingSnapshot(Generic[GenerationT, AccessT]):
    generation: GenerationT
    access: AccessT
    cancelled: bool
    age_seconds: float


@dataclass(frozen=True, slots=True)
class _LifecycleStateSnapshot(Generic[GenerationT, AccessT, CapabilityT]):
    """Internal lifecycle state sample retaining diagnostics alongside public state."""

    generation: GenerationT
    access: AccessT | None
    capability: CapabilityT | None
    pending: PendingSnapshot[GenerationT, AccessT] | None = None
    cleanup_registration_errors: tuple[str, ...] = ()


@dataclass(slots=True)
class _FailedCleanupRegistration:
    callback: Callable[[], None]
    diagnostic: str


class LifecycleStateMachine(Generic[GenerationT, AccessT, CapabilityT]):
    """Shared acquire/commit/release lifecycle state machine.

    Lifecycle state is represented as one explicit private state: ``_Idle``, ``_Acquiring``,
    ``_Current``, or ``_Draining``. Public committed state is always represented by one atomic
    ``Snapshot(generation, access, capability)``. An in-flight ``AcquireAttempt`` captures both the
    generation and requested access so release can fence by the complete ``(generation, access)``
    target before revoking pending work.

    Every in-flight acquisition owns a ``ResourceScope`` that follows it through revocation,
    commit, or abandonment. Domain adapters populate that scope as physical resources are obtained;
    the state machine keeps the committed access, capability, and scope associated in ``_Current``.
    Domain lifecycles retain responsibility for acquisition constraints, cleanup implementation,
    claim semantics, domain values, and notifications. This state machine owns the concurrency-
    sensitive authority/ownership relationship and invokes narrow callbacks while holding its state
    lock when cleanup-debt registration must stay atomic with a state transition.

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
        self._state: _State[GenerationT, AccessT, CapabilityT] = _Idle(issue_generation())
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
        state: _Acquiring[GenerationT, AccessT] | _Draining[GenerationT, AccessT],
    ) -> PendingSnapshot[GenerationT, AccessT]:
        return PendingSnapshot(
            generation=state.attempt.generation,
            access=state.attempt.access,
            cancelled=(
                isinstance(state, _Draining) or state.attempt.cancellation.is_set()
            ),
            age_seconds=max(0.0, monotonic() - state.started_at),
        )

    @staticmethod
    def _public_snapshot(
        state: _State[GenerationT, AccessT, CapabilityT],
    ) -> Snapshot[GenerationT, AccessT, CapabilityT]:
        if isinstance(state, _Current):
            return Snapshot(state.generation, state.access, state.capability)
        return Snapshot(state.generation)

    def _snapshot_locked(
        self,
    ) -> _LifecycleStateSnapshot[GenerationT, AccessT, CapabilityT]:
        state = self._state
        public = self._public_snapshot(state)
        pending = (
            self._pending_snapshot(state)
            if isinstance(state, (_Acquiring, _Draining))
            else None
        )
        return _LifecycleStateSnapshot(
            generation=public.generation,
            access=public.access,
            capability=public.capability,
            pending=pending,
            cleanup_registration_errors=tuple(
                item.diagnostic for item in self._failed_cleanup_registrations
            ),
        )

    def snapshot(self) -> Snapshot[GenerationT, AccessT, CapabilityT]:
        """Return one atomic committed-state snapshot without extending capability lifetime."""

        with self._lock:
            return self._public_snapshot(self._state)

    def read_diagnostics_snapshot(
        self,
    ) -> _LifecycleStateSnapshot[GenerationT, AccessT, CapabilityT]:
        """Return internal lifecycle diagnostics without exposing cleanup resources."""

        with self._lock:
            return self._snapshot_locked()

    def borrow_capability(self, expected: GenerationT) -> CapabilityT | None:
        """Return matching current capability as a point-in-time atomic borrow.

        The state lock protects the generation comparison and capability retrieval only. The
        returned capability is not leased and may become stale immediately after this method
        returns.
        """

        with self._lock:
            state = self._state
            if not isinstance(state, _Current) or expected != state.generation:
                return None
            return state.capability

    def begin_acquire(
        self,
        expected: GenerationT,
        access: AccessT,
        *,
        is_blocked: Callable[[], bool] | None = None,
    ) -> AcquireStartResult[GenerationT, AccessT, CapabilityT]:
        """Start an acquisition iff expected generation still owns the requested command."""

        if expected is None:
            raise TypeError("expected cannot be None")
        if access is None:
            raise TypeError("access cannot be None")
        if is_blocked is not None and not callable(is_blocked):
            raise TypeError("is_blocked must be callable or None")

        with self._lock:
            state = self._state
            if expected != state.generation:
                return GenerationMismatch(state.generation)
            if isinstance(state, _Current):
                return AcquireStartCurrent(self._public_snapshot(state))
            if isinstance(state, _Acquiring):
                return AcquireStartBusy(draining=False)
            if isinstance(state, _Draining):
                return AcquireStartBusy(draining=True)
            if not isinstance(state, _Idle):
                raise RuntimeError("unsupported lifecycle state")

            if not self._retry_cleanup_registrations_locked():
                return AcquireStartBlocked(
                    "lifecycle cleanup registration failed; retry required"
                )
            if is_blocked is not None and is_blocked():
                return AcquireStartBlocked()

            attempt = AcquireAttempt(
                generation=state.generation,
                access=access,
                cancellation=Event(),
                resource_scope=ResourceScope(),
            )
            self._state = _Acquiring(attempt=attempt)
            return attempt

    def abandon_acquire(
        self,
        attempt: AcquireAttempt[GenerationT, AccessT],
        *,
        before_clear: Callable[[], None] | None = None,
    ) -> AcquireAbandonResult[GenerationT]:
        """Finish an acquisition and report whether it still owned commit authority.

        A failing ``before_clear`` still retires the matching in-flight state. Its
        resource-retaining cleanup registration remains queued for retry and blocks new
        acquisitions until registration succeeds.
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

            outcome: AcquireAbandonResult[GenerationT] = (
                AcquireAttemptRevoked(state.generation)
                if matches_draining
                else AcquireAttemptAbandoned()
            )
            attempt.resource_scope.seal()
            try:
                if before_clear is not None:
                    self._register_cleanup_locked(before_clear)
            finally:
                self._state = _Idle(state.generation)
            return outcome

    def commit_acquire(
        self,
        attempt: AcquireAttempt[GenerationT, AccessT],
        capability: CapabilityT,
        *,
        on_superseded: Callable[[], None] | None = None,
    ) -> AcquireCommitResult[GenerationT, AccessT, CapabilityT]:
        """Finalize a commit and report the authority fact observed at linearization."""

        if not isinstance(attempt, AcquireAttempt):
            raise TypeError("attempt must be AcquireAttempt")
        if capability is None:
            raise ValueError("capability cannot be None")
        if on_superseded is not None and not callable(on_superseded):
            raise TypeError("on_superseded must be callable or None")

        with self._lock:
            state = self._state
            if isinstance(state, (_Acquiring, _Draining)) and state.attempt is attempt:
                attempt.resource_scope.seal()
            if isinstance(state, _Acquiring) and state.attempt is attempt:
                committed = Snapshot(state.generation, attempt.access, capability)
                self._state = _Current(
                    state.generation,
                    attempt.access,
                    capability,
                    attempt.resource_scope,
                )
                return AcquireAttemptCommitted(committed)

            if not isinstance(state, _Draining) or state.attempt is not attempt:
                raise RuntimeError("acquisition attempt is not current")
            try:
                if on_superseded is not None:
                    self._register_cleanup_locked(on_superseded)
            finally:
                self._state = _Idle(state.generation)
            return AcquireAttemptRevoked(state.generation)

    def release(
        self,
        expected: GenerationT,
        access: AccessT,
        *,
        on_access_release: Callable[[ResourceScope], None] | None = None,
        inconsistent_state_error: str = "lifecycle state is inconsistent",
    ) -> ReleaseResult[GenerationT, AccessT]:
        """Release only matching ``(generation, access)`` authority and advance generation."""

        if expected is None:
            raise TypeError("expected cannot be None")
        if access is None:
            raise TypeError("access cannot be None")
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
                return GenerationMismatch(state.generation)

            if isinstance(state, (_Idle, _Draining)):
                return ReleaseInactive()

            released_generation = state.generation
            if isinstance(state, _Acquiring):
                current_access = state.attempt.access
                if current_access != access:
                    return ReleaseAccessMismatch(current_access)

                next_generation = self._issue_generation()
                if next_generation == released_generation:
                    raise RuntimeError("issue_generation must return a fresh generation")

                # Logical revocation is immediate, but the old attempt remains represented until
                # obtain returns. New acquisition is therefore blocked in the new generation while
                # the revoked request drains.
                state.attempt.cancellation.set()
                self._state = _Draining(
                    generation=next_generation,
                    attempt=state.attempt,
                    started_at=state.started_at,
                )
                return ReleaseAcquisitionRevoked(next_generation)

            if not isinstance(state, _Current):
                raise RuntimeError(normalized_error)
            if state.access != access:
                return ReleaseAccessMismatch(state.access)

            next_generation = self._issue_generation()
            if next_generation == released_generation:
                raise RuntimeError("issue_generation must return a fresh generation")

            self._state = _Idle(next_generation)
            outcome = ReleaseAccessDetached(
                state.access,
                next_generation,
            )
            if on_access_release is not None:
                try:
                    self._register_cleanup_locked(
                        lambda: on_access_release(state.resource_scope)
                    )
                except Exception as exc:
                    raise CleanupRegistrationError(outcome) from exc
            return outcome


__all__ = [
    "LifecycleStateMachine",
    "PendingSnapshot",
]
