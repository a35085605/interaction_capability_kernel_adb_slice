from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import Event
from typing import Generic, TypeVar

from networking import TcpAddress
from adb._lifecycle import (
    AcquireBlocked,
    AcquireBusy,
    AcquireExisting,
    AcquireStarted,
    LifecycleAuthority,
    LifecycleDiagnostics,
    ReleaseAcquisitionRevoked,
    ReleaseGenerationMismatch,
    ReleaseInactive,
    ReleaseOwnershipDetached,
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
    """Expected failure while obtaining a server acquisition handle."""

    def __init__(self, diagnostic: str) -> None:
        self.diagnostic = _normalize_diagnostic(diagnostic)
        super().__init__(self.diagnostic)


class AdbServerAcquireInterruptedError(RuntimeError):
    """Cooperative interruption after the captured server generation was released."""

    def __init__(self) -> None:
        super().__init__("ADB server acquisition was interrupted")


HandleT = TypeVar("HandleT")


@dataclass(frozen=True, slots=True)
class _Ownership(Generic[HandleT]):
    handle: HandleT
    acquisition: AdbServerAcquisition


class AdbServerLifecycleTemplate(Generic[HandleT], ABC):
    """Template for one current server generation and its optional usable endpoint.

    Logical release is immediate: matching release advances the generation and detaches the
    current handle before returning. Physical cleanup is tracked independently as cleanup debt.
    Each retired resource gets one backend-local cleanup attempt before unresolved responsibility is
    offered to ``CleanupHandoff``. Accepted debt remains pending until completion is reported.
    Cleanup debt blocks a new acquisition only when both refer to the same server endpoint.
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
        self._authority: LifecycleAuthority[
            AdbServerGeneration, _Ownership[HandleT]
        ] = LifecycleAuthority(generation_issuer.issue)
        self._cleanup = CleanupCoordinator(cleanup_handoff)

    def read(self) -> AdbServerState:
        """Atomically return the current generation and its usable endpoint, if any."""

        state = self._authority.snapshot()
        ownership = state.ownership
        return AdbServerState(
            generation=state.generation,
            endpoint=None if ownership is None else ownership.acquisition.endpoint,
        )

    def read_diagnostics(self) -> LifecycleDiagnostics[AdbServerGeneration]:
        """Sample draining work and cleanup handoff state without exposing resources."""

        state = self._authority.snapshot()
        cleanup = self._cleanup.snapshot()
        return LifecycleDiagnostics(
            state.generation, state.pending, state.cleanup_registration_errors,
            cleanup.pending_count, cleanup.handoff_accepted_count, cleanup.handoff_errors,
        )

    @abstractmethod
    def _obtain_handle(
        self,
        endpoint_constraint: AdbServerEndpoint | None,
        cancellation: Event,
    ) -> tuple[HandleT, AdbServerEndpoint]:
        """Obtain an acquisition handle and its usable endpoint.

        Bound blocking operations and honor cancellation between them. Revocation deliberately
        retains the in-flight authority state until this method returns, preventing overlapping
        physical acquisitions.
        """

    @abstractmethod
    def _attempt_local_cleanup(self, handle: HandleT) -> object | None:
        """Attempt bounded local cleanup; return unresolved resource or ``None`` on success."""

    def _schedule_cleanup(
        self,
        handle: HandleT,
        endpoint: AdbServerEndpoint,
    ) -> None:
        self._register_cleanup(handle, endpoint)
        self._cleanup.process_pending()

    def _register_cleanup(
        self,
        handle: HandleT,
        endpoint: AdbServerEndpoint,
    ) -> None:
        """Locked, idempotent debt registration; cleanup processing starts after core unlock."""

        self._cleanup.register(
            handle,
            lambda: self._attempt_local_cleanup(handle),
            conflict_key=endpoint,
        )

    def _schedule_unresolved_cleanup(
        self,
        resource: object,
        endpoint: AdbServerEndpoint | None,
    ) -> None:
        self._cleanup.register_handoff(resource, conflict_key=endpoint)
        self._cleanup.process_pending()

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
        start = self._authority.begin_acquire(
            is_blocked=(
                None
                if endpoint_constraint is None
                else lambda: self._cleanup.has_conflict(endpoint_constraint)
            )
        )
        if isinstance(start, AcquireExisting):
            ownership = start.ownership
            if (
                endpoint_constraint is None
                or ownership.acquisition.endpoint == endpoint_constraint
            ):
                return AdbServerAcquireExisting(ownership.acquisition)
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
                or "ADB server lifecycle is cleaning a resource for the requested endpoint"
            )
        if not isinstance(start, AcquireStarted):
            raise TypeError("unsupported shared lifecycle acquire start")
        attempt = start
        token = attempt.token

        try:
            handle, endpoint = self._obtain_handle(endpoint_constraint, attempt.cancellation)
        except AdbServerAcquireInterruptedError as exc:
            revoked = self._authority.abandon_acquire(token)
            if revoked:
                return AdbServerAcquireSuperseded(attempt.generation)
            raise RuntimeError(
                "ADB server acquisition was interrupted without generation revocation"
            ) from exc
        except AdbServerAcquireError as exc:
            revoked = self._authority.abandon_acquire(token)
            if revoked:
                return AdbServerAcquireSuperseded(attempt.generation)
            return AdbServerAcquireFailed(exc.diagnostic)
        except BaseException:
            self._authority.abandon_acquire(token)
            raise

        if self._cleanup.has_conflict(endpoint):
            revoked = self._authority.abandon_acquire(
                token,
                before_clear=lambda: self._register_cleanup(handle, endpoint),
            )
            if revoked:
                return AdbServerAcquireSuperseded(attempt.generation)
            return AdbServerAcquireBlocked(
                "ADB server lifecycle obtained an endpoint whose prior resource is still cleaning"
            )

        if endpoint_constraint is not None and endpoint != endpoint_constraint:
            revoked = self._authority.abandon_acquire(
                token,
                before_clear=lambda: self._register_cleanup(handle, endpoint),
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
            ownership = _Ownership(handle, acquisition)
        except BaseException:
            self._authority.abandon_acquire(
                token,
                before_clear=lambda: self._register_cleanup(handle, endpoint),
            )
            raise

        committed = self._authority.commit_acquire(
            token,
            ownership,
            on_superseded=lambda: self._register_cleanup(handle, endpoint),
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
        def retire_ownership(ownership: _Ownership[HandleT]) -> None:
            self._register_cleanup(ownership.handle, ownership.acquisition.endpoint)

        release = self._authority.release(
            expected,
            on_owned_release=retire_ownership,
            inconsistent_state_error="ADB server lifecycle authority state is inconsistent",
        )
        self._cleanup.process_pending()
        if isinstance(release, ReleaseGenerationMismatch):
            ownership = release.ownership
            return AdbServerReleaseGenerationMismatch(
                current=None if ownership is None else ownership.acquisition,
                current_generation=release.current_generation,
            )
        if isinstance(release, ReleaseInactive):
            return AdbServerReleaseInactive(generation=release.generation)
        if isinstance(release, ReleaseAcquisitionRevoked):
            return AdbServerReleaseApplied(generation=release.generation)
        if isinstance(release, ReleaseOwnershipDetached):
            ownership = release.ownership
            return AdbServerReleaseApplied(
                generation=release.generation,
                acquisition=ownership.acquisition,
            )
        raise TypeError("unsupported shared lifecycle release decision")


__all__ = [
    "AdbServerAcquireError",
    "AdbServerAcquireInterruptedError",
    "AdbServerLifecycleTemplate",
]
