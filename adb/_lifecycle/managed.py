from __future__ import annotations

from collections.abc import Callable, Iterable
from threading import Event
from types import TracebackType
from typing import Generic, TypeVar

from adb._lifecycle.diagnostics import LifecycleDiagnostics
from adb._lifecycle.resource import ResourceScope
from adb._lifecycle.result import (
    AcquireAttempt,
    AcquireStartResult,
    ReleaseResult,
)
from adb._lifecycle.snapshot import Snapshot
from adb._lifecycle.state_machine import LifecycleStateMachine
from adb.cleanup import (
    CleanupCoordinator,
    CleanupHandoff,
    LocalCleanupAttempt,
    ResourceClaimConflict,
)


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")
CapabilityT = TypeVar("CapabilityT")


class AcquireAttemptGuard(Generic[GenerationT, AccessT, CapabilityT]):
    """Ensure one managed acquisition attempt is terminated exactly once.

    The attempt already owns its requested access. Domain acquisition, validation, failure mapping,
    and capability construction remain with the caller. Leaving the context without an explicit
    commit or abandon automatically abandons the attempt so unexpected exceptions cannot strand
    lifecycle state or owned resources.
    """

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

    def abandon(self) -> bool:
        """Abandon this attempt and report whether release had already revoked it."""

        self._finish_before_call()
        return self._managed.abandon_acquire(self._attempt)

    def commit(
        self,
        *,
        capability: CapabilityT,
    ) -> Snapshot[GenerationT, AccessT, CapabilityT] | None:
        """Commit the capability for this attempt's access if it still has authority."""

        if capability is None:
            # Keep the guard open so context exit still abandons the attempt.
            raise ValueError("capability cannot be None")
        self._finish_before_call()
        return self._managed.commit_acquire(
            self._attempt,
            capability=capability,
        )

    def _finish_before_call(self) -> None:
        if self._finished:
            raise RuntimeError("acquisition attempt guard is already finished")
        # State-machine finalization can apply its transition and then raise while registering
        # cleanup debt. Mark the guard first so __exit__ never attempts a second finalization.
        self._finished = True


class ManagedLifecycle(Generic[GenerationT, AccessT, CapabilityT]):
    """Coordinate atomic lifecycle state with cleanup-debt registration.

    ``LifecycleStateMachine`` owns generation fencing and one atomic committed
    ``Snapshot(generation, access, capability)``. ``ManagedLifecycle`` adds resource cleanup-debt
    registration while keeping physical cleanup outside lifecycle authority locks. Snapshot reads do
    not extend capability lifetime; ``borrow_capability()`` remains a generation-fenced
    point-in-time lookup for consumers that already hold a generation.
    """

    def __init__(
        self,
        issue_generation: Callable[[], GenerationT],
        *,
        cleanup_handoff: CleanupHandoff,
    ) -> None:
        self._state_machine: LifecycleStateMachine[
            GenerationT, AccessT, CapabilityT
        ] = LifecycleStateMachine(issue_generation)
        self._cleanup = CleanupCoordinator(cleanup_handoff)

    def read(self) -> Snapshot[GenerationT, AccessT, CapabilityT]:
        """Return one atomic generation/access/capability snapshot."""

        return self._state_machine.snapshot()

    def read_diagnostics(self) -> LifecycleDiagnostics[GenerationT, AccessT]:
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
        expected: GenerationT,
        access: AccessT,
        *,
        is_blocked: Callable[[], bool] | None = None,
    ) -> AcquireStartResult[GenerationT, AccessT, CapabilityT]:
        """Begin generation-fenced acquisition for one requested access."""

        return self._state_machine.begin_acquire(
            expected,
            access,
            is_blocked=is_blocked,
        )

    def guard_acquire(
        self,
        attempt: AcquireAttempt[GenerationT, AccessT],
    ) -> AcquireAttemptGuard[GenerationT, AccessT, CapabilityT]:
        """Guard one started attempt so every scope exit finalizes it exactly once."""

        return AcquireAttemptGuard(self, attempt)

    def abandon_acquire(self, attempt: AcquireAttempt[GenerationT, AccessT]) -> bool:
        """Abandon an attempt and atomically register every still-owned resource for cleanup."""

        return self._state_machine.abandon_acquire(
            attempt,
            before_clear=lambda: self._register_resource_scope(attempt.resource_scope),
        )

    def commit_acquire(
        self,
        attempt: AcquireAttempt[GenerationT, AccessT],
        *,
        capability: CapabilityT,
    ) -> Snapshot[GenerationT, AccessT, CapabilityT] | None:
        """Commit capability or clean up a superseded attempt."""

        return self._state_machine.commit_acquire(
            attempt,
            capability,
            on_superseded=lambda: self._register_resource_scope(attempt.resource_scope),
        )

    def borrow_capability(self, expected: GenerationT) -> CapabilityT | None:
        """Return the matching current capability without extending its lifecycle."""

        return self._state_machine.borrow_capability(expected)

    def release(
        self,
        expected: GenerationT,
        access: AccessT,
        *,
        inconsistent_state_error: str = "lifecycle state is inconsistent",
    ) -> ReleaseResult[GenerationT, AccessT]:
        """Release matching generation/access authority and register detached resources."""

        return self._state_machine.release(
            expected,
            access,
            on_access_release=self._register_resource_scope,
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
