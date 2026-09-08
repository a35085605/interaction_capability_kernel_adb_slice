from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from threading import Event
from typing import Protocol

from networking import TcpAddress

from adb._lifecycle import (
    LifecycleAcquireBlocked,
    LifecycleAcquireBusy,
    LifecycleAcquireOwned,
    LifecycleAuthorityCore,
    LifecycleDiagnostics,
    LifecyclePendingAcquire,
    LifecycleReleaseGenerationMismatch,
    LifecycleReleaseInactive,
    LifecycleReleaseOwned,
    LifecycleReleasePending,
)
from adb.cleanup import CleanupCoordinator, CleanupHandoff, LocalCleanupAttempt
from adb.transport_list.model import AdbTransportList
from adb.transport_list.watch.contract import (
    AdbTransportListWatchAcquisition,
    AdbTransportListWatchAcquireBlocked,
    AdbTransportListWatchAcquireCommitted,
    AdbTransportListWatchAcquireFailed,
    AdbTransportListWatchAcquireSuperseded,
    AdbTransportListWatchAcquireOutcome,
    AdbTransportListWatchAcquireExisting,
    AdbTransportListWatchReleaseApplied,
    AdbTransportListWatchReleaseInactive,
    AdbTransportListWatchReleaseGenerationMismatch,
    AdbTransportListWatchReleaseOutcome,
)
from adb.transport_list.watch.failure import AdbTransportListWatchFailure
from adb.transport_list.watch.generation import (
    AdbTransportListWatchGeneration,
    AdbTransportListWatchGenerationIssuer,
)
from adb.transport_list.watch.state import AdbTransportListWatchState
from adb.transport_list.watch.stream import AdbTransportListWatchStream


class AdbTransportListWatchAcquireError(RuntimeError):
    """Expected failure while obtaining a usable transport-list watch handle."""

    def __init__(self, failure: AdbTransportListWatchFailure) -> None:
        if not isinstance(failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")
        self.failure = failure
        super().__init__(failure.diagnostic or type(failure).__name__)


class AdbTransportListWatchAcquireInterruptedError(RuntimeError):
    """Cooperative interruption after the captured watch generation was released."""

    def __init__(self) -> None:
        super().__init__("ADB transport-list watch acquisition was interrupted")


class _AdbTransportListWatchHandle(AdbTransportListWatchStream, Protocol):
    """Lifecycle-private physical watch handle with lifecycle cleanup operations."""

    def close(self) -> object | None:
        """Attempt local cleanup; return unresolved resource or ``None``."""
        ...


class _AdbTransportListWatchStreamView:
    """Narrow producer capability over a lifecycle-owned physical handle."""

    __slots__ = ("__handle",)

    def __init__(self, handle: _AdbTransportListWatchHandle) -> None:
        self.__handle = handle

    @property
    def initial(self) -> AdbTransportList:
        return self.__handle.initial

    def updates(self) -> Iterator[AdbTransportList]:
        return self.__handle.updates()


@dataclass(frozen=True, slots=True)
class _Ownership:
    """Lifecycle-private physical ownership plus producer-facing data capability."""

    handle: _AdbTransportListWatchHandle
    stream: AdbTransportListWatchStream
    acquisition: AdbTransportListWatchAcquisition


class AdbTransportListWatchLifecycleTemplate(ABC):
    """Template for one current watch generation and its optional physical handle.

    Logical release advances the generation and detaches producer authority immediately. Physical
    cleanup is tracked independently as cleanup debt. Each retired handle gets one backend-local
    cleanup attempt before unresolved responsibility is offered to ``CleanupHandoff``. Accepted debt
    remains pending until completion is reported, and blocks only its own server endpoint.
    """

    def __init__(
        self,
        generation_issuer: AdbTransportListWatchGenerationIssuer,
        *,
        cleanup_handoff: CleanupHandoff,
    ) -> None:
        if not isinstance(generation_issuer, AdbTransportListWatchGenerationIssuer):
            raise TypeError(
                "generation_issuer must be AdbTransportListWatchGenerationIssuer"
            )
        if not isinstance(cleanup_handoff, CleanupHandoff):
            raise TypeError("cleanup_handoff must satisfy CleanupHandoff")
        self._core: LifecycleAuthorityCore[
            AdbTransportListWatchGeneration, _Ownership
        ] = LifecycleAuthorityCore(generation_issuer.issue)
        self._cleanup = CleanupCoordinator(cleanup_handoff)

    def read(self) -> AdbTransportListWatchState:
        """Atomically return current generation and usable endpoint metadata, if any."""

        state = self._core.snapshot()
        ownership = state.ownership
        return AdbTransportListWatchState(
            generation=state.generation,
            endpoint=None if ownership is None else ownership.acquisition.endpoint,
        )

    def read_diagnostics(self) -> LifecycleDiagnostics[AdbTransportListWatchGeneration]:
        """Sample draining work and cleanup handoff state without exposing resources."""

        state = self._core.snapshot()
        cleanup = self._cleanup.snapshot()
        return LifecycleDiagnostics(
            state.generation, state.pending, state.retirement_errors,
            cleanup.pending_count, cleanup.handoff_accepted_count, cleanup.handoff_errors,
        )

    @abstractmethod
    def _obtain_handle(
        self,
        endpoint: TcpAddress,
        cancellation: Event,
    ) -> _AdbTransportListWatchHandle:
        """Obtain a fully usable lifecycle-owned physical watch handle.

        Bound blocking operations and honor cancellation between them. Revocation deliberately
        retains pending until this method returns, preventing overlapping physical acquisitions.
        """

    def _schedule_cleanup(
        self,
        handle: _AdbTransportListWatchHandle,
        endpoint: TcpAddress,
    ) -> None:
        self._register_cleanup(handle, endpoint)
        self._cleanup.process_pending()

    def _register_cleanup(
        self,
        handle: _AdbTransportListWatchHandle,
        endpoint: TcpAddress,
    ) -> None:
        """Locked, idempotent debt registration; cleanup processing starts after core unlock."""

        self._cleanup.register(handle, lambda: handle.close(), conflict_key=endpoint)

    def _schedule_resource_cleanup(
        self,
        resource: object,
        local_cleanup: LocalCleanupAttempt,
        endpoint: TcpAddress,
    ) -> None:
        self._cleanup.register(resource, local_cleanup, conflict_key=endpoint)
        self._cleanup.process_pending()

    def _schedule_unresolved_cleanup(
        self,
        resource: object,
        endpoint: TcpAddress,
    ) -> None:
        self._cleanup.register_handoff(resource, conflict_key=endpoint)
        self._cleanup.process_pending()

    def _borrow_stream(
        self,
        expected: AdbTransportListWatchGeneration,
    ) -> AdbTransportListWatchStream | None:
        """Return the narrow producer stream while matching watch authority is current."""

        if not isinstance(expected, AdbTransportListWatchGeneration):
            raise TypeError("expected must be AdbTransportListWatchGeneration")

        state = self._core.snapshot()
        ownership = state.ownership
        if (
            expected != state.generation
            or ownership is None
            or ownership.acquisition.generation != expected
        ):
            return None
        return ownership.stream

    def acquire(
        self,
        endpoint: TcpAddress,
    ) -> AdbTransportListWatchAcquireOutcome:
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")

        self._cleanup.process_pending()
        try:
            return self._acquire(endpoint)
        finally:
            self._cleanup.process_pending()

    def _acquire(self, endpoint: TcpAddress) -> AdbTransportListWatchAcquireOutcome:
        start = self._core.begin_acquire(
            is_blocked=lambda: self._cleanup.has_conflict(endpoint)
        )
        if isinstance(start, LifecycleAcquireOwned):
            ownership = start.ownership
            if ownership.acquisition.endpoint == endpoint:
                return AdbTransportListWatchAcquireExisting(ownership.acquisition)
            return AdbTransportListWatchAcquireBlocked(
                "ADB transport-list watch lifecycle already retains a different endpoint"
            )
        if isinstance(start, LifecycleAcquireBusy):
            return AdbTransportListWatchAcquireBlocked(
                "ADB transport-list watch lifecycle is draining a revoked acquisition"
                if start.draining
                else "ADB transport-list watch lifecycle is busy with another acquisition"
            )
        if isinstance(start, LifecycleAcquireBlocked):
            return AdbTransportListWatchAcquireBlocked(
                start.diagnostic
                or "ADB transport-list watch lifecycle is cleaning a resource for this endpoint"
            )
        if not isinstance(start, LifecyclePendingAcquire):
            raise TypeError("unsupported shared lifecycle acquire start")
        pending = start

        try:
            handle = self._obtain_handle(endpoint, pending.cancellation)
        except AdbTransportListWatchAcquireInterruptedError as exc:
            revoked = self._core.abandon_acquire(pending)
            if revoked:
                return AdbTransportListWatchAcquireSuperseded(pending.generation)
            raise RuntimeError(
                "ADB transport-list watch acquisition was interrupted without generation "
                "revocation"
            ) from exc
        except AdbTransportListWatchAcquireError as exc:
            revoked = self._core.abandon_acquire(pending)
            if revoked:
                return AdbTransportListWatchAcquireSuperseded(pending.generation)
            return AdbTransportListWatchAcquireFailed(exc.failure)
        except BaseException:
            self._core.abandon_acquire(pending)
            raise

        if self._cleanup.has_conflict(endpoint):
            revoked = self._core.abandon_acquire(
                pending,
                before_clear=lambda: self._register_cleanup(handle, endpoint),
            )
            if revoked:
                return AdbTransportListWatchAcquireSuperseded(pending.generation)
            return AdbTransportListWatchAcquireBlocked(
                "ADB transport-list watch lifecycle obtained a resource whose prior endpoint "
                "is still cleaning"
            )

        try:
            acquisition = AdbTransportListWatchAcquisition(
                endpoint=endpoint,
                generation=pending.generation,
            )
            ownership = _Ownership(
                handle=handle,
                stream=_AdbTransportListWatchStreamView(handle),
                acquisition=acquisition,
            )
        except BaseException:
            self._core.abandon_acquire(
                pending,
                before_clear=lambda: self._register_cleanup(handle, endpoint),
            )
            raise

        committed = self._core.commit_acquire(
            pending,
            ownership,
            on_superseded=lambda: self._register_cleanup(handle, endpoint),
        )
        if committed:
            return AdbTransportListWatchAcquireCommitted(acquisition)

        return AdbTransportListWatchAcquireSuperseded(pending.generation)

    def release(
        self,
        expected: AdbTransportListWatchGeneration,
    ) -> AdbTransportListWatchReleaseOutcome:
        if not isinstance(expected, AdbTransportListWatchGeneration):
            raise TypeError("expected must be AdbTransportListWatchGeneration")

        try:
            return self._release(expected)
        finally:
            self._cleanup.process_pending()

    def _release(
        self, expected: AdbTransportListWatchGeneration
    ) -> AdbTransportListWatchReleaseOutcome:
        def retire_ownership(ownership: _Ownership) -> None:
            self._register_cleanup(ownership.handle, ownership.acquisition.endpoint)

        release = self._core.release(
            expected,
            on_owned_release=retire_ownership,
            inconsistent_state_error=(
                "ADB transport-list watch lifecycle authority state is inconsistent"
            ),
        )
        if isinstance(release, LifecycleReleaseGenerationMismatch):
            ownership = release.ownership
            return AdbTransportListWatchReleaseGenerationMismatch(
                current=None if ownership is None else ownership.acquisition,
                current_generation=release.current_generation,
            )
        if isinstance(release, LifecycleReleaseInactive):
            return AdbTransportListWatchReleaseInactive(release.generation)
        if isinstance(release, LifecycleReleasePending):
            return AdbTransportListWatchReleaseApplied(generation=release.generation)
        if isinstance(release, LifecycleReleaseOwned):
            ownership = release.ownership
            return AdbTransportListWatchReleaseApplied(
                generation=release.generation,
                acquisition=ownership.acquisition,
            )
        raise TypeError("unsupported shared lifecycle release decision")


__all__ = [
    "AdbTransportListWatchAcquireError",
    "AdbTransportListWatchAcquireInterruptedError",
    "AdbTransportListWatchLifecycleTemplate",
]
