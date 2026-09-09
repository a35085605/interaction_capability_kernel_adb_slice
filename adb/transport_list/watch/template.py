from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from threading import Event
from typing import Protocol

from networking import TcpAddress

from adb._lifecycle import (
    AcquireBlocked,
    AcquireBusy,
    AcquireExisting,
    AcquireAttempt,
    LifecycleStateMachine,
    LifecycleDiagnostics,
    ReleaseAcquisitionRevoked,
    ReleaseGenerationMismatch,
    ReleaseInactive,
    ReleaseResourceDetached,
    ResourceScope,
)
from adb.cleanup import CleanupCoordinator, CleanupHandoff
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
class _WatchResource:
    """Committed producer capability; physical ownership lives in the lifecycle scope."""

    stream: AdbTransportListWatchStream
    acquisition: AdbTransportListWatchAcquisition


class AdbTransportListWatchLifecycleTemplate(ABC):
    """Template for one current watch generation and its physical resource scope.

    Watch/client sockets carry no exclusivity claim merely because they connect to the same server
    endpoint. Cleanup debt is therefore tracked by ownership but does not block a new watch on an
    equal access endpoint. This avoids treating access information as a resource conflict key.
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
        self._state_machine: LifecycleStateMachine[
            AdbTransportListWatchGeneration, _WatchResource
        ] = LifecycleStateMachine(generation_issuer.issue)
        self._cleanup = CleanupCoordinator(cleanup_handoff)

    def read(self) -> AdbTransportListWatchState:
        """Atomically return current generation and usable endpoint metadata, if any."""

        state = self._state_machine.snapshot()
        resource = state.resource
        return AdbTransportListWatchState(
            generation=state.generation,
            endpoint=None if resource is None else resource.acquisition.endpoint,
        )

    def read_diagnostics(self) -> LifecycleDiagnostics[AdbTransportListWatchGeneration]:
        """Sample draining work and cleanup handoff state without exposing resources."""

        state = self._state_machine.snapshot()
        cleanup = self._cleanup.snapshot()
        return LifecycleDiagnostics(
            state.generation, state.pending, state.cleanup_registration_errors,
            cleanup.pending_count, cleanup.handoff_accepted_count, cleanup.handoff_errors,
        )

    @abstractmethod
    def _obtain_handle(
        self,
        endpoint: TcpAddress,
        cancellation: Event,
        resources: ResourceScope,
    ) -> _AdbTransportListWatchHandle:
        """Obtain a fully usable handle while recording any earlier resources in ``resources``."""

    def _register_resource_scope(self, resources: ResourceScope) -> None:
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

    def _schedule_cleanup(self, handle: _AdbTransportListWatchHandle) -> None:
        self._cleanup.register(handle, lambda: handle.close())
        self._cleanup.process_pending()

    def _borrow_stream(
        self,
        expected: AdbTransportListWatchGeneration,
    ) -> AdbTransportListWatchStream | None:
        """Return the narrow producer stream while the matching watch generation is current."""

        if not isinstance(expected, AdbTransportListWatchGeneration):
            raise TypeError("expected must be AdbTransportListWatchGeneration")

        state = self._state_machine.snapshot()
        resource = state.resource
        if (
            expected != state.generation
            or resource is None
            or resource.acquisition.generation != expected
        ):
            return None
        return resource.stream

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
        start = self._state_machine.begin_acquire()
        if isinstance(start, AcquireExisting):
            resource = start.resource
            if resource.acquisition.endpoint == endpoint:
                return AdbTransportListWatchAcquireExisting(resource.acquisition)
            return AdbTransportListWatchAcquireBlocked(
                "ADB transport-list watch lifecycle already retains a different endpoint"
            )
        if isinstance(start, AcquireBusy):
            return AdbTransportListWatchAcquireBlocked(
                "ADB transport-list watch lifecycle is draining a revoked acquisition"
                if start.draining
                else "ADB transport-list watch lifecycle is busy with another acquisition"
            )
        if isinstance(start, AcquireBlocked):
            return AdbTransportListWatchAcquireBlocked(
                start.diagnostic or "ADB transport-list watch lifecycle acquisition is blocked"
            )
        if not isinstance(start, AcquireAttempt):
            raise TypeError("unsupported shared lifecycle acquire start")
        attempt = start
        resources = attempt.resource_scope
        register_scope = lambda: self._register_resource_scope(resources)

        try:
            handle = self._obtain_handle(endpoint, attempt.cancellation, resources)
            resources.adopt(handle, lambda: handle.close())
        except AdbTransportListWatchAcquireInterruptedError as exc:
            revoked = self._state_machine.abandon_acquire(
                attempt, before_clear=register_scope
            )
            if revoked:
                return AdbTransportListWatchAcquireSuperseded(attempt.generation)
            raise RuntimeError(
                "ADB transport-list watch acquisition was interrupted without generation "
                "revocation"
            ) from exc
        except AdbTransportListWatchAcquireError as exc:
            revoked = self._state_machine.abandon_acquire(
                attempt, before_clear=register_scope
            )
            if revoked:
                return AdbTransportListWatchAcquireSuperseded(attempt.generation)
            return AdbTransportListWatchAcquireFailed(exc.failure)
        except BaseException:
            self._state_machine.abandon_acquire(attempt, before_clear=register_scope)
            raise

        try:
            acquisition = AdbTransportListWatchAcquisition(
                endpoint=endpoint,
                generation=attempt.generation,
            )
            resource = _WatchResource(
                stream=_AdbTransportListWatchStreamView(handle),
                acquisition=acquisition,
            )
        except BaseException:
            self._state_machine.abandon_acquire(
                attempt,
                before_clear=register_scope,
            )
            raise

        committed = self._state_machine.commit_acquire(
            attempt,
            resource,
            on_superseded=register_scope,
        )
        if committed:
            return AdbTransportListWatchAcquireCommitted(acquisition)

        return AdbTransportListWatchAcquireSuperseded(attempt.generation)

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
        def retire_resource(resource: _WatchResource, resources: ResourceScope) -> None:
            self._register_resource_scope(resources)

        release = self._state_machine.release(
            expected,
            on_resource_release=retire_resource,
            inconsistent_state_error=(
                "ADB transport-list watch lifecycle state is inconsistent"
            ),
        )
        if isinstance(release, ReleaseGenerationMismatch):
            resource = release.resource
            return AdbTransportListWatchReleaseGenerationMismatch(
                current=None if resource is None else resource.acquisition,
                current_generation=release.current_generation,
            )
        if isinstance(release, ReleaseInactive):
            return AdbTransportListWatchReleaseInactive(release.generation)
        if isinstance(release, ReleaseAcquisitionRevoked):
            return AdbTransportListWatchReleaseApplied(generation=release.generation)
        if isinstance(release, ReleaseResourceDetached):
            resource = release.resource
            return AdbTransportListWatchReleaseApplied(
                generation=release.generation,
                acquisition=resource.acquisition,
            )
        raise TypeError("unsupported shared lifecycle release decision")


__all__ = [
    "AdbTransportListWatchAcquireError",
    "AdbTransportListWatchAcquireInterruptedError",
    "AdbTransportListWatchLifecycleTemplate",
]
