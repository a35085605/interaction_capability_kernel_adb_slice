from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import Event
from typing import Iterable

from networking import TcpAddress
from adb._lifecycle import (
    AcquireBlocked,
    AcquireBusy,
    AcquireExisting,
    AcquireStarted,
    LifecycleStateMachine,
    LifecycleDiagnostics,
    ReleaseAcquisitionRevoked,
    ReleaseGenerationMismatch,
    ReleaseInactive,
    ReleaseResourceDetached,
    ResourceScope,
)
from adb.cleanup import CleanupCoordinator, CleanupHandoff
from adb.server.endpoint import AdbServerEndpoint
from adb.server.generation import AdbServerGeneration, AdbServerGenerationIssuer
from adb.server.state import AdbServerState
from adb.server.lifecycle.errors import AdbServerLifecycleConsistencyError
from adb.server.lifecycle.contract import (
    AdbServerAcquisition,
    AdbServerAcquireBlocked,
    AdbServerAcquireCommitted,
    AdbServerAcquireFailed,
    AdbServerAcquireSuperseded,
    AdbServerAcquireOutcome,
    AdbServerAcquireExisting,
    AdbServerReleaseApplied,
    AdbServerReleaseInactive,
    AdbServerReleaseGenerationMismatch,
    AdbServerReleaseOutcome,
)


def _normalize_diagnostic(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("diagnostic must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("diagnostic cannot be empty")
    return normalized


class AdbServerAcquireError(RuntimeError):
    """Expected failure while obtaining usable ADB server access."""

    def __init__(self, diagnostic: str) -> None:
        self.diagnostic = _normalize_diagnostic(diagnostic)
        super().__init__(self.diagnostic)


class AdbServerAcquireInterruptedError(RuntimeError):
    """Cooperative interruption after the captured server generation was released."""

    def __init__(self) -> None:
        super().__init__("ADB server acquisition was interrupted")


@dataclass(frozen=True, slots=True)
class _ServerResource:
    """One committed acquisition; physical ownership lives in its lifecycle resource scope."""

    acquisition: AdbServerAcquisition


class AdbServerLifecycleTemplate(ABC):
    """Template for one current ADB server acquisition and its owned resource scope.

    Access information, physical ownership, and resource claims are deliberately separate. Adapters
    add resources to the acquisition ``ResourceScope`` as soon as they are obtained and define the
    claims those resources retain. A committed acquisition stores only caller-facing access data;
    the shared lifecycle keeps its resource scope associated with that acquisition.

    Logical release is immediate. Matching release advances the generation and atomically registers
    every still-owned resource as cleanup debt while holding lifecycle authority. Physical cleanup
    runs after that lock is released. Cleanup completion, not handoff acceptance, retires a resource
    and therefore its claims.
    """

    def __init__(
        self,
        generation_issuer: AdbServerGenerationIssuer,
        *,
        cleanup_handoff: CleanupHandoff,
    ) -> None:
        if not isinstance(generation_issuer, AdbServerGenerationIssuer):
            raise TypeError("generation_issuer must be AdbServerGenerationIssuer")
        if not isinstance(cleanup_handoff, CleanupHandoff):
            raise TypeError("cleanup_handoff must satisfy CleanupHandoff")
        self._state_machine: LifecycleStateMachine[
            AdbServerGeneration, _ServerResource
        ] = LifecycleStateMachine(generation_issuer.issue)
        self._cleanup = CleanupCoordinator(cleanup_handoff)

    def read(self) -> AdbServerState:
        """Atomically return the current generation and its usable endpoint, if any."""

        state = self._state_machine.snapshot()
        resource = state.resource
        return AdbServerState(
            generation=state.generation,
            endpoint=None if resource is None else resource.acquisition.endpoint,
        )

    def read_diagnostics(self) -> LifecycleDiagnostics[AdbServerGeneration]:
        """Sample draining work and cleanup handoff state without exposing resources."""

        state = self._state_machine.snapshot()
        cleanup = self._cleanup.snapshot()
        return LifecycleDiagnostics(
            state.generation, state.pending, state.cleanup_registration_errors,
            cleanup.pending_count, cleanup.handoff_accepted_count, cleanup.handoff_errors,
        )

    @abstractmethod
    def _obtain_access(
        self,
        endpoint_constraint: AdbServerEndpoint | None,
        cancellation: Event,
        resources: ResourceScope,
    ) -> AdbServerEndpoint:
        """Obtain usable access while recording physical ownership in ``resources``.

        Resource-producing operations must adopt returned resources before later work can fail.
        Bound blocking operations and honor cancellation between them. Revocation deliberately retains
        in-flight scope until this method returns, preventing overlapping physical acquisitions.
        """

    def _requested_resource_claims(
        self,
        endpoint_constraint: AdbServerEndpoint | None,
    ) -> tuple[object, ...]:
        """Return claims known before acquisition starts; empty means no pre-acquire claim."""

        return ()

    @abstractmethod
    def _resource_claims_conflict(self, existing: object, requested: object) -> bool:
        """Return whether two adapter-defined resource claims are mutually exclusive."""

    def _cleanup_has_conflict(self, claims: Iterable[object]) -> bool:
        return self._cleanup.has_conflict(claims, self._resource_claims_conflict)

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

    def acquire(
        self,
        endpoint_constraint: AdbServerEndpoint | None = None,
    ) -> AdbServerAcquireOutcome:
        if endpoint_constraint is not None and not isinstance(endpoint_constraint, TcpAddress):
            raise TypeError("endpoint_constraint must be TcpAddress or None")

        self._cleanup.process_pending()
        try:
            return self._acquire(endpoint_constraint)
        finally:
            self._cleanup.process_pending()

    def _acquire(
        self, endpoint_constraint: AdbServerEndpoint | None
    ) -> AdbServerAcquireOutcome:
        requested_claims = self._requested_resource_claims(endpoint_constraint)
        start = self._state_machine.begin_acquire(
            is_blocked=(
                None
                if not requested_claims
                else lambda: self._cleanup_has_conflict(requested_claims)
            )
        )
        if isinstance(start, AcquireExisting):
            resource = start.resource
            if (
                endpoint_constraint is None
                or resource.acquisition.endpoint == endpoint_constraint
            ):
                return AdbServerAcquireExisting(resource.acquisition)
            return AdbServerAcquireBlocked(
                "ADB server lifecycle already retains a different endpoint"
            )
        if isinstance(start, AcquireBusy):
            return AdbServerAcquireBlocked(
                "ADB server lifecycle is draining a revoked acquisition"
                if start.draining else "ADB server lifecycle is busy with another acquisition"
            )
        if isinstance(start, AcquireBlocked):
            return AdbServerAcquireBlocked(
                start.diagnostic
                or "ADB server lifecycle is cleaning a conflicting owned resource"
            )
        if not isinstance(start, AcquireStarted):
            raise TypeError("unsupported shared lifecycle acquire start")
        attempt = start
        token = attempt.token
        resources = attempt.resource_scope
        register_scope = lambda: self._register_resource_scope(resources)

        try:
            endpoint = self._obtain_access(
                endpoint_constraint,
                attempt.cancellation,
                resources,
            )
        except AdbServerAcquireInterruptedError as exc:
            revoked = self._state_machine.abandon_acquire(
                token, before_clear=register_scope
            )
            if revoked:
                return AdbServerAcquireSuperseded(attempt.generation)
            raise RuntimeError(
                "ADB server acquisition was interrupted without generation revocation"
            ) from exc
        except AdbServerAcquireError as exc:
            revoked = self._state_machine.abandon_acquire(
                token, before_clear=register_scope
            )
            if revoked:
                return AdbServerAcquireSuperseded(attempt.generation)
            return AdbServerAcquireFailed(exc.diagnostic)
        except BaseException:
            self._state_machine.abandon_acquire(token, before_clear=register_scope)
            raise

        if self._cleanup_has_conflict(resources.claims()):
            revoked = self._state_machine.abandon_acquire(
                token,
                before_clear=register_scope,
            )
            if revoked:
                return AdbServerAcquireSuperseded(attempt.generation)
            return AdbServerAcquireBlocked(
                "ADB server lifecycle obtained resources whose claims conflict with pending cleanup"
            )

        if endpoint_constraint is not None and endpoint != endpoint_constraint:
            revoked = self._state_machine.abandon_acquire(
                token,
                before_clear=register_scope,
            )
            if revoked:
                return AdbServerAcquireSuperseded(attempt.generation)
            raise AdbServerLifecycleConsistencyError(
                "endpoint-constrained ADB server acquisition returned a different endpoint"
            )

        try:
            acquisition = AdbServerAcquisition(
                endpoint=endpoint,
                generation=attempt.generation,
            )
            resource = _ServerResource(acquisition)
        except BaseException:
            self._state_machine.abandon_acquire(
                token,
                before_clear=register_scope,
            )
            raise

        committed = self._state_machine.commit_acquire(
            token,
            resource,
            on_superseded=register_scope,
        )
        if committed:
            return AdbServerAcquireCommitted(acquisition)

        return AdbServerAcquireSuperseded(attempt.generation)

    def release(self, expected: AdbServerGeneration) -> AdbServerReleaseOutcome:
        if not isinstance(expected, AdbServerGeneration):
            raise TypeError("expected must be AdbServerGeneration")

        try:
            return self._release(expected)
        finally:
            self._cleanup.process_pending()

    def _release(self, expected: AdbServerGeneration) -> AdbServerReleaseOutcome:
        def retire_resource(resource: _ServerResource, resources: ResourceScope) -> None:
            self._register_resource_scope(resources)

        release = self._state_machine.release(
            expected,
            on_resource_release=retire_resource,
            inconsistent_state_error="ADB server lifecycle state is inconsistent",
        )
        if isinstance(release, ReleaseGenerationMismatch):
            resource = release.resource
            return AdbServerReleaseGenerationMismatch(
                current=None if resource is None else resource.acquisition,
                current_generation=release.current_generation,
            )
        if isinstance(release, ReleaseInactive):
            return AdbServerReleaseInactive(generation=release.generation)
        if isinstance(release, ReleaseAcquisitionRevoked):
            return AdbServerReleaseApplied(generation=release.generation)
        if isinstance(release, ReleaseResourceDetached):
            resource = release.resource
            return AdbServerReleaseApplied(
                generation=release.generation,
                acquisition=resource.acquisition,
            )
        raise TypeError("unsupported shared lifecycle release decision")


__all__ = [
    "AdbServerAcquireError",
    "AdbServerAcquireInterruptedError",
    "AdbServerLifecycleTemplate",
]
