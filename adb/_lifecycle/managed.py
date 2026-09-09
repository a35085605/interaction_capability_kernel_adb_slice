from __future__ import annotations

from collections.abc import Callable, Iterable
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


__all__ = ["ManagedLifecycle"]
