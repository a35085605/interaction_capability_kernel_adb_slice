from __future__ import annotations

from collections.abc import Callable, Iterable
from threading import Event
from types import TracebackType
from typing import Generic, TypeVar

from adb._lifecycle.diagnostics import LifecycleDiagnostics
from adb._lifecycle.resource import ResourceScope
from adb._lifecycle.result import AcquireAttempt, AcquireStartResult, ReleaseResult
from adb._lifecycle.snapshot import LifecycleSnapshot
from adb._lifecycle.state_machine import LifecycleStateMachine
from adb.cleanup import (
    CleanupCoordinator,
    CleanupHandoff,
    LocalCleanupAttempt,
    ResourceClaimConflict,
)


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")


class AcquireAttemptGuard(Generic[GenerationT, AccessT]):
    """Ensure one managed acquisition attempt is terminated exactly once.

    The guard owns only attempt finalization. Domain acquisition, validation, failure mapping, and
    access construction remain with the caller. Leaving the context without an explicit commit or
    abandon automatically abandons the attempt so unexpected exceptions cannot strand lifecycle
    state or owned resources.
    """

    __slots__ = ("_managed", "_attempt", "_finished")

    def __init__(
        self,
        managed: ManagedLifecycle[GenerationT, AccessT],
        attempt: AcquireAttempt[GenerationT],
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
    def cancellation(self) -> Event:
        return self._attempt.cancellation

    @property
    def resources(self) -> ResourceScope:
        return self._attempt.resource_scope

    def __enter__(self) -> AcquireAttemptGuard[GenerationT, AccessT]:
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

    def abandon(self) -> bool:
        """Abandon this attempt and report whether release had already revoked it."""

        self._finish_before_call()
        return self._managed.abandon_acquire(self._attempt)

    def commit(self, access: AccessT) -> bool:
        """Commit access if this attempt still has authority; otherwise finalize as superseded."""

        if access is None:
            # Keep the guard open so context exit still abandons the attempt.
            raise ValueError("access cannot be None")
        self._finish_before_call()
        return self._managed.commit_acquire(self._attempt, access)

    def _finish_before_call(self) -> None:
        if self._finished:
            raise RuntimeError("acquisition attempt guard is already finished")
        # State-machine finalization can apply its transition and then raise while registering
        # cleanup debt. Mark the guard first so __exit__ never attempts a second finalization.
        self._finished = True


class ManagedLifecycle(Generic[GenerationT, AccessT]):
    """Coordinate lifecycle authority with cleanup-debt registration.

    ``LifecycleStateMachine`` remains responsible for generation fencing and atomic state
    transitions. ``CleanupCoordinator`` remains responsible for cleanup debt and physical cleanup.
    This composition owns the narrow protocol between them: every abandoned, superseded, or
    released resource scope is registered as cleanup debt while lifecycle authority is locked,
    while physical cleanup is advanced only through explicit calls outside that lock.
    """

    def __init__(
        self,
        issue_generation: Callable[[], GenerationT],
        *,
        cleanup_handoff: CleanupHandoff,
    ) -> None:
        self._state_machine: LifecycleStateMachine[
            GenerationT, AccessT
        ] = LifecycleStateMachine(issue_generation)
        self._cleanup = CleanupCoordinator(cleanup_handoff)

    def snapshot(self) -> LifecycleSnapshot[GenerationT, AccessT | None]:
        return self._state_machine.snapshot()

    def read_diagnostics(self) -> LifecycleDiagnostics[GenerationT]:
        """Sample lifecycle and cleanup state without treating the samples as one transaction."""

        state = self._state_machine.read_diagnostics_snapshot()
        cleanup = self._cleanup.snapshot()
        return LifecycleDiagnostics(
            state.generation,
            state.pending,
            state.cleanup_registration_errors,
            cleanup.pending_count,
            cleanup.handoff_accepted_count,
            cleanup.handoff_errors,
        )

    def begin_acquire(
        self,
        *,
        is_blocked: Callable[[], bool] | None = None,
    ) -> AcquireStartResult[GenerationT, AccessT]:
        return self._state_machine.begin_acquire(is_blocked=is_blocked)

    def guard_acquire(
        self,
        attempt: AcquireAttempt[GenerationT],
    ) -> AcquireAttemptGuard[GenerationT, AccessT]:
        """Guard one started attempt so every scope exit finalizes it exactly once."""

        return AcquireAttemptGuard(self, attempt)

    def abandon_acquire(self, attempt: AcquireAttempt[GenerationT]) -> bool:
        """Abandon an attempt and atomically register every still-owned resource for cleanup."""

        return self._state_machine.abandon_acquire(
            attempt,
            before_clear=lambda: self._register_resource_scope(attempt.resource_scope),
        )

    def commit_acquire(
        self,
        attempt: AcquireAttempt[GenerationT],
        access: AccessT,
    ) -> bool:
        """Commit access or atomically register the superseded attempt's resources for cleanup."""

        return self._state_machine.commit_acquire(
            attempt,
            access,
            on_superseded=lambda: self._register_resource_scope(attempt.resource_scope),
        )

    def release(
        self,
        expected: GenerationT,
        *,
        inconsistent_state_error: str = "lifecycle state is inconsistent",
    ) -> ReleaseResult[GenerationT, AccessT]:
        """Release matching authority and atomically register detached resources for cleanup."""

        return self._state_machine.release(
            expected,
            on_access_release=(
                lambda _access, resources: self._register_resource_scope(resources)
            ),
            inconsistent_state_error=inconsistent_state_error,
        )

    def has_cleanup_conflict(
        self,
        requested_claims: Iterable[object],
        conflicts: ResourceClaimConflict,
    ) -> bool:
        return self._cleanup.has_conflict(requested_claims, conflicts)

    def register_cleanup(
        self,
        resource: object,
        local_cleanup: LocalCleanupAttempt,
        *,
        identity: object | None = None,
        claims: Iterable[object] = (),
    ) -> None:
        """Register cleanup debt that originates outside a lifecycle state transition."""

        self._cleanup.register(
            resource,
            local_cleanup,
            identity=identity,
            claims=claims,
        )

    def process_cleanup(self) -> None:
        """Advance currently registered cleanup work outside lifecycle authority locks."""

        self._cleanup.process_pending()

    def _register_resource_scope(self, resources: ResourceScope) -> None:
        """Register all still-owned resources without executing cleanup work."""

        if not isinstance(resources, ResourceScope):
            raise TypeError("resources must be ResourceScope")
        for ownership in resources.snapshot():
            if ownership.handoff_only or ownership.local_cleanup is None:
                self._cleanup.register_handoff(
                    ownership.resource,
                    identity=ownership.identity,
                    claims=ownership.claims,
                )
            else:
                self._cleanup.register(
                    ownership.resource,
                    ownership.local_cleanup,
                    identity=ownership.identity,
                    claims=ownership.claims,
                )


__all__ = ["AcquireAttemptGuard", "ManagedLifecycle"]
