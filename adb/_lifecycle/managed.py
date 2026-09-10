from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from threading import Event
from types import TracebackType
from typing import Generic, TypeVar

from adb._lifecycle.diagnostics import LifecycleDiagnostics
from adb._lifecycle.resource import ResourceScope
from adb._lifecycle.result import (
    AcquireAttempt,
    AcquireStartExisting,
    AcquireStartResult,
    CleanupRegistrationError,
    ReleaseAccessDetached,
    ReleaseAcquisitionRevoked,
    ReleaseGenerationMismatch,
    ReleaseInactive,
    ReleaseResult,
)
from adb._lifecycle.snapshot import LifecycleSnapshot
from adb._lifecycle.state_machine import LifecycleStateMachine
from adb.cleanup import (
    CleanupCoordinator,
    CleanupHandoff,
    LocalCleanupAttempt,
    ResourceClaimConflict,
)


GenerationT = TypeVar("GenerationT")
PublicAccessT = TypeVar("PublicAccessT")
CapabilityT = TypeVar("CapabilityT")


@dataclass(frozen=True, slots=True)
class _Committed(Generic[PublicAccessT, CapabilityT]):
    """Lifecycle-private pairing of public metadata and an operation capability."""

    public_access: PublicAccessT
    capability: CapabilityT

    def __post_init__(self) -> None:
        if self.public_access is None:
            raise ValueError("public_access cannot be None")
        if self.capability is None:
            raise ValueError("capability cannot be None")


class AcquireAttemptGuard(Generic[GenerationT, PublicAccessT, CapabilityT]):
    """Ensure one managed acquisition attempt is terminated exactly once.

    The guard owns only attempt finalization. Domain acquisition, validation, failure mapping, and
    committed-value construction remain with the caller. Leaving the context without an explicit
    commit or abandon automatically abandons the attempt so unexpected exceptions cannot strand
    lifecycle state or owned resources.
    """

    __slots__ = ("_managed", "_attempt", "_finished")

    def __init__(
        self,
        managed: ManagedLifecycle[GenerationT, PublicAccessT, CapabilityT],
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

    def __enter__(self) -> AcquireAttemptGuard[GenerationT, PublicAccessT, CapabilityT]:
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
        public_access: PublicAccessT,
        capability: CapabilityT,
    ) -> bool:
        """Commit public access and capability together if this attempt still has authority."""

        if public_access is None:
            # Keep the guard open so context exit still abandons the attempt.
            raise ValueError("public_access cannot be None")
        if capability is None:
            # Keep the guard open so context exit still abandons the attempt.
            raise ValueError("capability cannot be None")
        self._finish_before_call()
        return self._managed.commit_acquire(
            self._attempt,
            public_access=public_access,
            capability=capability,
        )

    def _finish_before_call(self) -> None:
        if self._finished:
            raise RuntimeError("acquisition attempt guard is already finished")
        # State-machine finalization can apply its transition and then raise while registering
        # cleanup debt. Mark the guard first so __exit__ never attempts a second finalization.
        self._finished = True


class ManagedLifecycle(Generic[GenerationT, PublicAccessT, CapabilityT]):
    """Coordinate public access, operation capability, and cleanup-debt registration.

    ``LifecycleStateMachine`` remains responsible for generation fencing and atomic state
    transitions. It stores one private committed payload containing both caller-facing public access
    and the capability consumers need for operations. ``ManagedLifecycle`` owns projection of that
    payload: snapshots and lifecycle outcomes expose only public access, while
    ``borrow_capability()`` performs a generation-fenced point-in-time capability lookup.

    ``CleanupCoordinator`` remains responsible for cleanup debt and physical cleanup. Every
    abandoned, superseded, or released resource scope is registered as cleanup debt while lifecycle
    authority is locked, while physical cleanup is advanced only through explicit calls outside that
    lock.
    """

    def __init__(
        self,
        issue_generation: Callable[[], GenerationT],
        *,
        cleanup_handoff: CleanupHandoff,
    ) -> None:
        self._state_machine: LifecycleStateMachine[
            GenerationT, _Committed[PublicAccessT, CapabilityT]
        ] = LifecycleStateMachine(issue_generation)
        self._cleanup = CleanupCoordinator(cleanup_handoff)

    @staticmethod
    def _project_snapshot(
        snapshot: LifecycleSnapshot[
            GenerationT, _Committed[PublicAccessT, CapabilityT] | None
        ],
    ) -> LifecycleSnapshot[GenerationT, PublicAccessT | None]:
        committed = snapshot.access
        return LifecycleSnapshot(
            snapshot.generation,
            None if committed is None else committed.public_access,
        )

    @staticmethod
    def _project_detached(
        outcome: ReleaseAccessDetached[
            GenerationT, _Committed[PublicAccessT, CapabilityT]
        ],
    ) -> ReleaseAccessDetached[GenerationT, PublicAccessT]:
        return ReleaseAccessDetached(
            generation=outcome.generation,
            access=outcome.access.public_access,
        )

    @classmethod
    def _project_release(
        cls,
        outcome: ReleaseResult[
            GenerationT, _Committed[PublicAccessT, CapabilityT]
        ],
    ) -> ReleaseResult[GenerationT, PublicAccessT]:
        if isinstance(outcome, ReleaseGenerationMismatch):
            committed = outcome.access
            return ReleaseGenerationMismatch(
                current_generation=outcome.current_generation,
                access=None if committed is None else committed.public_access,
            )
        if isinstance(outcome, (ReleaseInactive, ReleaseAcquisitionRevoked)):
            return outcome
        if isinstance(outcome, ReleaseAccessDetached):
            return cls._project_detached(outcome)
        raise TypeError("unsupported lifecycle release decision")

    def snapshot(self) -> LifecycleSnapshot[GenerationT, PublicAccessT | None]:
        """Return one atomic generation/public-access pairing."""

        return self._project_snapshot(self._state_machine.snapshot())

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
    ) -> AcquireStartResult[GenerationT, PublicAccessT]:
        """Begin acquisition or expose only the public access of an existing commitment."""

        result = self._state_machine.begin_acquire(is_blocked=is_blocked)
        if isinstance(result, AcquireStartExisting):
            snapshot = result.snapshot
            return AcquireStartExisting(
                LifecycleSnapshot(
                    snapshot.generation,
                    snapshot.access.public_access,
                )
            )
        return result

    def guard_acquire(
        self,
        attempt: AcquireAttempt[GenerationT],
    ) -> AcquireAttemptGuard[GenerationT, PublicAccessT, CapabilityT]:
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
        *,
        public_access: PublicAccessT,
        capability: CapabilityT,
    ) -> bool:
        """Commit public access and capability or clean up a superseded attempt."""

        committed = _Committed(public_access=public_access, capability=capability)
        return self._state_machine.commit_acquire(
            attempt,
            committed,
            on_superseded=lambda: self._register_resource_scope(attempt.resource_scope),
        )

    def borrow_capability(self, expected: GenerationT) -> CapabilityT | None:
        """Return the matching current capability without extending its lifecycle.

        Generation comparison and capability retrieval are atomic. The returned capability is only
        known to be current at the instant it is borrowed; a concurrent release may revoke its
        generation immediately afterward.
        """

        committed = self._state_machine.borrow_current(expected)
        return None if committed is None else committed.capability

    def release(
        self,
        expected: GenerationT,
        *,
        inconsistent_state_error: str = "lifecycle state is inconsistent",
    ) -> ReleaseResult[GenerationT, PublicAccessT]:
        """Release matching authority without exposing the committed capability."""

        projected_error: CleanupRegistrationError | None = None
        projected_cause: BaseException | None = None
        try:
            outcome = self._state_machine.release(
                expected,
                on_access_release=(
                    lambda _committed, resources: self._register_resource_scope(resources)
                ),
                inconsistent_state_error=inconsistent_state_error,
            )
        except CleanupRegistrationError as exc:
            # The state transition has already happened. Rebuild the exception outside this
            # handler so neither ``outcome`` nor exception context exposes the private capability.
            projected_error = CleanupRegistrationError(self._project_detached(exc.outcome))
            projected_cause = exc.__cause__
        else:
            return self._project_release(outcome)

        assert projected_error is not None
        if projected_cause is None:
            raise projected_error from None
        raise projected_error from projected_cause

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
