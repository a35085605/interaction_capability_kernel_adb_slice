from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event, Lock
from time import monotonic
from typing import Generic, TypeAlias, TypeVar

from adb._lifecycle.resource import GLOBAL_RESOURCE_POOL, ResourcePool, ResourceScope
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
    """Current generation projects one usable capability and its resource-pool scope."""

    generation: GenerationT
    access: AccessT
    capability: CapabilityT
    resource_scope: ResourceScope


@dataclass(frozen=True, slots=True)
class _Draining(Generic[GenerationT, AccessT]):
    """A revoked acquisition is draining after authority advanced to a new generation.

    Its resource scope is already retired but intentionally remains unsealed until the resource-
    producing acquisition returns. Any resource arriving during that window is therefore registered
    directly in the global pool as retired.
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
    """Internal lifecycle state sample; resource state lives in ``ResourcePool``."""

    generation: GenerationT
    access: AccessT | None
    capability: CapabilityT | None
    pending: PendingSnapshot[GenerationT, AccessT] | None = None


class LifecycleStateMachine(Generic[GenerationT, AccessT, CapabilityT]):
    """Shared acquire/commit/release lifecycle state machine.

    Resource ownership is not copied into a later cleanup-debt model. Every acquisition receives a
    ``ResourceScope`` projected from one shared ``ResourcePool`` and resource-producing code
    registers into that pool immediately. Release and abandonment only retire pool state; physical
    cleanup is deliberately outside this state machine.
    """

    def __init__(
        self,
        issue_generation: Callable[[], GenerationT],
        *,
        resource_pool: ResourcePool = GLOBAL_RESOURCE_POOL,
    ) -> None:
        if not callable(issue_generation):
            raise TypeError("issue_generation must be callable")
        if not isinstance(resource_pool, ResourcePool):
            raise TypeError("resource_pool must be ResourcePool")
        self._issue_generation = issue_generation
        self._resource_pool = resource_pool
        self._lock = Lock()
        self._state: _State[GenerationT, AccessT, CapabilityT] = _Idle(issue_generation())

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
        )

    def snapshot(self) -> Snapshot[GenerationT, AccessT, CapabilityT]:
        """Return one atomic committed-state snapshot without extending capability lifetime."""

        with self._lock:
            return self._public_snapshot(self._state)

    def read_diagnostics_snapshot(
        self,
    ) -> _LifecycleStateSnapshot[GenerationT, AccessT, CapabilityT]:
        with self._lock:
            return self._snapshot_locked()

    def borrow_capability(self, expected: GenerationT) -> CapabilityT | None:
        """Return a matching current capability as a point-in-time atomic borrow."""

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
            if is_blocked is not None and is_blocked():
                return AcquireStartBlocked()

            attempt = AcquireAttempt(
                generation=state.generation,
                access=access,
                cancellation=Event(),
                resource_scope=self._resource_pool.open_scope(),
            )
            self._state = _Acquiring(attempt=attempt)
            return attempt

    def abandon_acquire(
        self,
        attempt: AcquireAttempt[GenerationT, AccessT],
    ) -> AcquireAbandonResult[GenerationT]:
        """Finish an acquisition and retire every resource registered by the attempt."""

        if not isinstance(attempt, AcquireAttempt):
            raise TypeError("attempt must be AcquireAttempt")

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
            attempt.resource_scope.retire_all()
            attempt.resource_scope.seal()
            self._state = _Idle(state.generation)
            return outcome

    def commit_acquire(
        self,
        attempt: AcquireAttempt[GenerationT, AccessT],
        capability: CapabilityT,
    ) -> AcquireCommitResult[GenerationT, AccessT, CapabilityT]:
        """Commit the capability or retire a resource scope whose authority was revoked."""

        if not isinstance(attempt, AcquireAttempt):
            raise TypeError("attempt must be AcquireAttempt")
        if capability is None:
            raise ValueError("capability cannot be None")

        with self._lock:
            state = self._state
            if isinstance(state, _Acquiring) and state.attempt is attempt:
                attempt.resource_scope.seal()
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
            attempt.resource_scope.retire_all()
            attempt.resource_scope.seal()
            self._state = _Idle(state.generation)
            return AcquireAttemptRevoked(state.generation)

    def release(
        self,
        expected: GenerationT,
        access: AccessT,
        *,
        inconsistent_state_error: str = "lifecycle state is inconsistent",
    ) -> ReleaseResult[GenerationT, AccessT]:
        """Release matching authority, advance generation, and retire its resource scope."""

        if expected is None:
            raise TypeError("expected cannot be None")
        if access is None:
            raise TypeError("access cannot be None")
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

                # Retire first. If the acquisition thread registers another resource concurrently,
                # it either lands before this operation and is retired here, or lands afterward and
                # inherits the scope's retired state from the pool.
                state.attempt.resource_scope.retire_all()
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

            state.resource_scope.retire_all()
            self._state = _Idle(next_generation)
            return ReleaseAccessDetached(next_generation)


__all__ = [
    "LifecycleStateMachine",
    "PendingSnapshot",
]
