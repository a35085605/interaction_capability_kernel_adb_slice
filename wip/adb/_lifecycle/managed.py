from __future__ import annotations

from collections.abc import Callable, Iterable
from threading import Event
from types import TracebackType
from typing import Generic, TypeVar

from adb._lifecycle.resource import (
    GLOBAL_RESOURCE_POOL,
    ResourceClaimConflict,
    ResourcePool,
    ResourceScope,
)
from adb._lifecycle.result import (
    AcquireAbandonResult,
    AcquireAttempt,
    AcquireCommitResult,
    AcquireStartResult,
    ReleaseResult,
)
from adb._lifecycle.snapshot import Snapshot
from adb._lifecycle.state_machine import LifecycleStateMachine


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")
CapabilityT = TypeVar("CapabilityT")


class AcquireAttemptGuard(Generic[GenerationT, AccessT, CapabilityT]):
    """Ensure one managed acquisition attempt is terminated exactly once."""

    __slots__ = ("_managed", "_attempt", "_finished")

    def __init__(
        self,
        managed: ManagedLifecycle[GenerationT, AccessT, CapabilityT],
        attempt: AcquireAttempt[GenerationT, AccessT],
    ) -> None:
        if not isinstance(attempt, AcquireAttempt):
            raise TypeError("attempt must be AcquireAttempt")
        self._managed = managed
        self._attempt = attempt
        self._finished = False

    @property
    def generation(self) -> GenerationT:
        return self._attempt.generation

    @property
    def access(self) -> AccessT:
        return self._attempt.access

    @property
    def cancellation(self) -> Event:
        return self._attempt.cancellation

    @property
    def resources(self) -> ResourceScope:
        return self._attempt.resource_scope

    def __enter__(self) -> AcquireAttemptGuard[GenerationT, AccessT, CapabilityT]:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if not self._finished:
            self._finish_before_call()
            self._managed.abandon_acquire(self._attempt)
        return False

    def abandon(self) -> AcquireAbandonResult[GenerationT]:
        self._finish_before_call()
        return self._managed.abandon_acquire(self._attempt)

    def commit(
        self,
        *,
        capability: CapabilityT,
    ) -> AcquireCommitResult[GenerationT, AccessT, CapabilityT]:
        if capability is None:
            raise ValueError("capability cannot be None")
        self._finish_before_call()
        return self._managed.commit_acquire(
            self._attempt,
            capability=capability,
        )

    def _finish_before_call(self) -> None:
        if self._finished:
            raise RuntimeError("acquisition attempt guard is already finished")
        self._finished = True


class ManagedLifecycle(Generic[GenerationT, AccessT, CapabilityT]):
    """Coordinate lifecycle authority with one shared resource pool.

    Resource-producing code registers directly into the attempt's ``ResourceScope``. Commit keeps
    that scope as a projection of pool state; release and abandonment only mark its resources
    retired. No cleanup debt or handoff model exists here.
    """

    def __init__(
        self,
        issue_generation: Callable[[], GenerationT],
        *,
        resource_pool: ResourcePool = GLOBAL_RESOURCE_POOL,
    ) -> None:
        if not isinstance(resource_pool, ResourcePool):
            raise TypeError("resource_pool must be ResourcePool")
        self._resource_pool = resource_pool
        self._state_machine: LifecycleStateMachine[
            GenerationT, AccessT, CapabilityT
        ] = LifecycleStateMachine(
            issue_generation,
            resource_pool=resource_pool,
        )

    @property
    def resource_pool(self) -> ResourcePool:
        return self._resource_pool

    def read(self) -> Snapshot[GenerationT, AccessT, CapabilityT]:
        return self._state_machine.snapshot()

    def begin_acquire(
        self,
        expected: GenerationT,
        access: AccessT,
        *,
        is_blocked: Callable[[], bool] | None = None,
    ) -> AcquireStartResult[GenerationT, AccessT, CapabilityT]:
        return self._state_machine.begin_acquire(
            expected,
            access,
            is_blocked=is_blocked,
        )

    def guard_acquire(
        self,
        attempt: AcquireAttempt[GenerationT, AccessT],
    ) -> AcquireAttemptGuard[GenerationT, AccessT, CapabilityT]:
        return AcquireAttemptGuard(self, attempt)

    def abandon_acquire(
        self,
        attempt: AcquireAttempt[GenerationT, AccessT],
    ) -> AcquireAbandonResult[GenerationT]:
        return self._state_machine.abandon_acquire(attempt)

    def commit_acquire(
        self,
        attempt: AcquireAttempt[GenerationT, AccessT],
        *,
        capability: CapabilityT,
    ) -> AcquireCommitResult[GenerationT, AccessT, CapabilityT]:
        return self._state_machine.commit_acquire(attempt, capability)

    def borrow_capability(self, expected: GenerationT) -> CapabilityT | None:
        return self._state_machine.borrow_capability(expected)

    def release(
        self,
        expected: GenerationT,
        access: AccessT,
        *,
        inconsistent_state_error: str = "lifecycle state is inconsistent",
    ) -> ReleaseResult[GenerationT, AccessT]:
        return self._state_machine.release(
            expected,
            access,
            inconsistent_state_error=inconsistent_state_error,
        )

    def has_resource_conflict(
        self,
        requested_claims: Iterable[object],
        conflicts: ResourceClaimConflict,
        *,
        exclude_scope: ResourceScope | None = None,
    ) -> bool:
        return self._resource_pool.has_conflict(
            requested_claims,
            conflicts,
            exclude_scope=exclude_scope,
        )


__all__ = ["AcquireAttemptGuard", "ManagedLifecycle"]
