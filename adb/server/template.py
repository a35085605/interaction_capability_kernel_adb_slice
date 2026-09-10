from __future__ import annotations

from abc import ABC, abstractmethod
from threading import Event
from typing import Iterable

from networking import TcpAddress
from adb._lifecycle import (
    AcquireAbandonResult,
    AcquireAttempt,
    AcquireAttemptAbandoned,
    AcquireAttemptCommitted,
    AcquireAttemptRevoked,
    AcquireAccessMismatch,
    AcquireBlocked,
    AcquireCommitted,
    AcquireExisting,
    AcquireFailed,
    AcquireStartBlocked,
    AcquireStartBusy,
    AcquireStartCurrent,
    AcquireSuperseded,
    GenerationMismatch,
    LifecycleDiagnostics,
    ManagedLifecycle,
    ReleaseAccessDetached,
    ReleaseAccessMismatch,
    ReleaseAcquisitionRevoked,
    ReleaseInactive,
    ResourceScope,
)
from adb.cleanup import CleanupHandoff
from adb.server.failure import AdbServerLaunchFailure
from adb.server.generation import AdbServerGeneration, AdbServerGenerationIssuer
from adb.server.state import AdbServerState
from adb.server.lifecycle import (
    AdbServerAccess,
    AdbServerAcquireOutcome,
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


def _superseded_after_abandon(
    result: AcquireAbandonResult[AdbServerGeneration],
) -> AcquireSuperseded[AdbServerGeneration] | None:
    """Translate a shared finalization fact into the server acquire outcome, if revoked."""

    if isinstance(result, AcquireAttemptRevoked):
        return AcquireSuperseded(result.current_generation)
    if isinstance(result, AcquireAttemptAbandoned):
        return None
    raise TypeError("unsupported shared lifecycle acquire abandonment")


class AdbServerLifecycleTemplate(ABC):
    """Template for one current ADB server access and its owned resource scope.

    The requested access enters lifecycle state before resource-producing acquisition begins. This
    lets acquire and release both fence on the complete ``(generation, access)`` target, including
    cancellation of an in-flight acquisition. A committed state atomically pairs that access with
    its operation capability in the public ``Snapshot``.

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
        self._managed: ManagedLifecycle[
            AdbServerGeneration, AdbServerAccess, TcpAddress
        ] = ManagedLifecycle(
            generation_issuer.issue,
            cleanup_handoff=cleanup_handoff,
        )

    def read(self) -> AdbServerState:
        """Return one atomic generation/access/server-capability snapshot."""

        return self._managed.read()

    def read_diagnostics(
        self,
    ) -> LifecycleDiagnostics[AdbServerGeneration, AdbServerAccess]:
        """Sample draining work and cleanup handoff state without exposing resources."""

        return self._managed.read_diagnostics()

    @abstractmethod
    def _obtain_access(
        self,
        server_address: TcpAddress,
        cancellation: Event,
        resources: ResourceScope,
    ) -> TcpAddress:
        """Obtain usable access while recording physical ownership in ``resources``.

        Resource-producing operations must adopt returned resources before later work can fail.
        Bound blocking operations and honor cancellation between them. Revocation deliberately
        retains in-flight scope until this method returns, preventing overlapping physical
        acquisitions.
        """

    def _requested_resource_claims(
        self,
        server_address: TcpAddress,
    ) -> tuple[object, ...]:
        """Return claims known before acquisition starts; empty means no pre-acquire claim."""

        return ()

    @abstractmethod
    def _resource_claims_conflict(self, existing: object, requested: object) -> bool:
        """Return whether two adapter-defined resource claims are mutually exclusive."""

    def _cleanup_has_conflict(self, claims: Iterable[object]) -> bool:
        return self._managed.has_cleanup_conflict(claims, self._resource_claims_conflict)

    def acquire(
        self,
        expected_generation: AdbServerGeneration,
        access: AdbServerAccess,
    ) -> AdbServerAcquireOutcome:
        if not isinstance(expected_generation, AdbServerGeneration):
            raise TypeError("expected_generation must be AdbServerGeneration")
        if not isinstance(access, AdbServerAccess):
            raise TypeError("access must be AdbServerAccess")

        self._managed.process_cleanup()
        try:
            return self._acquire(expected_generation, access)
        finally:
            self._managed.process_cleanup()

    def _acquire(
        self,
        expected_generation: AdbServerGeneration,
        access: AdbServerAccess,
    ) -> AdbServerAcquireOutcome:
        server_address = access.server_address
        requested_claims = self._requested_resource_claims(server_address)
        start = self._managed.begin_acquire(
            expected_generation,
            access,
            is_blocked=(
                None
                if not requested_claims
                else lambda: self._cleanup_has_conflict(requested_claims)
            ),
        )
        if isinstance(start, GenerationMismatch):
            return start
        if isinstance(start, AcquireStartCurrent):
            snapshot = start.snapshot
            if snapshot.access == access:
                return AcquireExisting(snapshot)
            assert snapshot.access is not None
            return AcquireAccessMismatch(snapshot.access)
        if isinstance(start, AcquireStartBusy):
            return AcquireBlocked(
                "ADB server lifecycle is draining a revoked acquisition"
                if start.draining else "ADB server lifecycle is busy with another acquisition"
            )
        if isinstance(start, AcquireStartBlocked):
            return AcquireBlocked(
                start.diagnostic
                or "ADB server lifecycle is cleaning a conflicting owned resource"
            )
        if not isinstance(start, AcquireAttempt):
            raise TypeError("unsupported shared lifecycle acquire start")
        with self._managed.guard_acquire(start) as attempt:
            try:
                obtained_server_address = self._obtain_access(
                    server_address,
                    attempt.cancellation,
                    attempt.resources,
                )
            except AdbServerAcquireInterruptedError as exc:
                superseded = _superseded_after_abandon(attempt.abandon())
                if superseded is not None:
                    return superseded
                raise RuntimeError(
                    "ADB server acquisition was interrupted without generation revocation"
                ) from exc
            except AdbServerAcquireError as exc:
                superseded = _superseded_after_abandon(attempt.abandon())
                if superseded is not None:
                    return superseded
                return AcquireFailed(AdbServerLaunchFailure(exc.diagnostic))

            if self._cleanup_has_conflict(attempt.resources.claims()):
                superseded = _superseded_after_abandon(attempt.abandon())
                if superseded is not None:
                    return superseded
                return AcquireBlocked(
                    "ADB server lifecycle obtained resources whose claims conflict with "
                    "pending cleanup"
                )

            if obtained_server_address != server_address:
                superseded = _superseded_after_abandon(attempt.abandon())
                if superseded is not None:
                    return superseded
                raise RuntimeError(
                    "ADB server acquisition returned a different server address"
                )

            finalized = attempt.commit(capability=obtained_server_address)
            if isinstance(finalized, AcquireAttemptCommitted):
                return AcquireCommitted(finalized.snapshot)
            if isinstance(finalized, AcquireAttemptRevoked):
                return AcquireSuperseded(finalized.current_generation)
            raise TypeError("unsupported shared lifecycle acquire commit")

    def release(
        self,
        expected_generation: AdbServerGeneration,
        access: AdbServerAccess,
    ) -> AdbServerReleaseOutcome:
        if not isinstance(expected_generation, AdbServerGeneration):
            raise TypeError("expected_generation must be AdbServerGeneration")
        if not isinstance(access, AdbServerAccess):
            raise TypeError("access must be AdbServerAccess")

        try:
            return self._release(expected_generation, access)
        finally:
            self._managed.process_cleanup()

    def _release(
        self,
        expected_generation: AdbServerGeneration,
        access: AdbServerAccess,
    ) -> AdbServerReleaseOutcome:
        release = self._managed.release(
            expected_generation,
            access,
            inconsistent_state_error="ADB server lifecycle state is inconsistent",
        )
        if isinstance(
            release,
            (
                GenerationMismatch,
                ReleaseAccessMismatch,
                ReleaseInactive,
                ReleaseAcquisitionRevoked,
                ReleaseAccessDetached,
            ),
        ):
            return release
        raise TypeError("unsupported shared lifecycle release decision")


__all__ = [
    "AdbServerAcquireError",
    "AdbServerAcquireInterruptedError",
    "AdbServerLifecycleTemplate",
]
