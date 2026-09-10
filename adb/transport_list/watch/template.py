from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from threading import Event
from typing import Protocol

from networking import TcpAddress

from adb._lifecycle import (
    AcquireAttempt,
    AcquireBlocked,
    AcquireCommitted,
    AcquireExisting,
    AcquireFailed,
    AcquireStartBlocked,
    AcquireStartBusy,
    AcquireStartExisting,
    AcquireSuperseded,
    LifecycleDiagnostics,
    LifecycleSnapshot,
    ManagedLifecycle,
    ReleaseAccessDetached,
    ReleaseAcquisitionRevoked,
    ReleaseGenerationMismatch,
    ReleaseInactive,
    ResourceScope,
)
from adb.cleanup import CleanupHandoff
from adb.transport_list.model import AdbTransportList
from adb.transport_list.watch.lifecycle import (
    AdbTransportListWatchAccess,
    AdbTransportListWatchAcquireOutcome,
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
    """Expected failure while establishing usable transport-list watch access."""

    def __init__(self, failure: AdbTransportListWatchFailure) -> None:
        if not isinstance(failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")
        self.failure = failure
        super().__init__(failure.diagnostic or type(failure).__name__)


class AdbTransportListWatchAcquireInterruptedError(RuntimeError):
    """Cooperative interruption after the captured watch generation was released."""

    def __init__(self) -> None:
        super().__init__("ADB transport-list watch acquisition was interrupted")


class _AdbTransportListWatchResource(AdbTransportListWatchStream, Protocol):
    """Lifecycle-private physical watch resource with lifecycle cleanup operations."""

    def close(self) -> object | None:
        """Attempt local cleanup; return unresolved resource or ``None``."""
        ...


class _AdbTransportListWatchStreamView:
    """Narrow producer capability over a lifecycle-owned physical watch resource."""

    __slots__ = ("__resource",)

    def __init__(self, resource: _AdbTransportListWatchResource) -> None:
        self.__resource = resource

    @property
    def initial(self) -> AdbTransportList:
        return self.__resource.initial

    def updates(self) -> Iterator[AdbTransportList]:
        return self.__resource.updates()


class AdbTransportListWatchLifecycleTemplate(ABC):
    """Template for one current watch generation and its physical resource scope.

    Watch/client sockets carry no exclusivity claim merely because they connect to the same server
    server address. Cleanup debt is therefore tracked by ownership but does not block a new watch on an
    equal access server address. This avoids treating access information as a resource conflict key.
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
        self._managed: ManagedLifecycle[
            AdbTransportListWatchGeneration,
            AdbTransportListWatchAccess,
            AdbTransportListWatchStream,
        ] = ManagedLifecycle(
            generation_issuer.issue,
            cleanup_handoff=cleanup_handoff,
        )

    def read(self) -> AdbTransportListWatchState:
        """Atomically return current generation and single-consumer watch stream, if any."""

        state = self._managed.capability_snapshot()
        return AdbTransportListWatchState(
            generation=state.generation,
            capability=state.capability,
        )

    def read_diagnostics(self) -> LifecycleDiagnostics[AdbTransportListWatchGeneration]:
        """Sample draining work and cleanup handoff state without exposing resources."""

        return self._managed.read_diagnostics()

    @abstractmethod
    def _obtain_resource(
        self,
        server_address: TcpAddress,
        cancellation: Event,
        resources: ResourceScope,
    ) -> _AdbTransportListWatchResource:
        """Obtain a fully usable watch resource while recording earlier resources in ``resources``."""

    def _schedule_cleanup(self, resource: _AdbTransportListWatchResource) -> None:
        self._managed.register_cleanup(resource, lambda: resource.close())
        self._managed.process_cleanup()

    def acquire(
        self,
        server_address: TcpAddress,
    ) -> AdbTransportListWatchAcquireOutcome:
        if not isinstance(server_address, TcpAddress):
            raise TypeError("server_address must be TcpAddress")

        self._managed.process_cleanup()
        try:
            return self._acquire(server_address)
        finally:
            self._managed.process_cleanup()

    def _acquire(self, server_address: TcpAddress) -> AdbTransportListWatchAcquireOutcome:
        start = self._managed.begin_acquire()
        if isinstance(start, AcquireStartExisting):
            snapshot = start.snapshot
            if snapshot.access.server_address == server_address:
                return AcquireExisting(snapshot)
            return AcquireBlocked(
                "ADB transport-list watch lifecycle already retains a different server address"
            )
        if isinstance(start, AcquireStartBusy):
            return AcquireBlocked(
                "ADB transport-list watch lifecycle is draining a revoked acquisition"
                if start.draining
                else "ADB transport-list watch lifecycle is busy with another acquisition"
            )
        if isinstance(start, AcquireStartBlocked):
            return AcquireBlocked(
                start.diagnostic or "ADB transport-list watch lifecycle acquisition is blocked"
            )
        if not isinstance(start, AcquireAttempt):
            raise TypeError("unsupported shared lifecycle acquire start")
        with self._managed.guard_acquire(start) as attempt:
            try:
                resource = self._obtain_resource(
                    server_address,
                    attempt.cancellation,
                    attempt.resources,
                )
                attempt.resources.adopt(resource, lambda: resource.close())
            except AdbTransportListWatchAcquireInterruptedError as exc:
                revoked = attempt.abandon()
                if revoked:
                    return AcquireSuperseded(attempt.generation)
                raise RuntimeError(
                    "ADB transport-list watch acquisition was interrupted without generation "
                    "revocation"
                ) from exc
            except AdbTransportListWatchAcquireError as exc:
                revoked = attempt.abandon()
                if revoked:
                    return AcquireSuperseded(attempt.generation)
                return AcquireFailed(exc.failure)

            public_access = AdbTransportListWatchAccess(server_address=server_address)
            capability = _AdbTransportListWatchStreamView(resource)
            committed = attempt.commit(
                public_access=public_access,
                capability=capability,
            )
            if committed:
                return AcquireCommitted(
                    LifecycleSnapshot(attempt.generation, public_access)
                )

            return AcquireSuperseded(attempt.generation)

    def release(
        self,
        expected: AdbTransportListWatchGeneration,
    ) -> AdbTransportListWatchReleaseOutcome:
        if not isinstance(expected, AdbTransportListWatchGeneration):
            raise TypeError("expected must be AdbTransportListWatchGeneration")

        try:
            return self._release(expected)
        finally:
            self._managed.process_cleanup()

    def _release(
        self, expected: AdbTransportListWatchGeneration
    ) -> AdbTransportListWatchReleaseOutcome:
        release = self._managed.release(
            expected,
            inconsistent_state_error=(
                "ADB transport-list watch lifecycle state is inconsistent"
            ),
        )
        if isinstance(
            release,
            (
                ReleaseGenerationMismatch,
                ReleaseInactive,
                ReleaseAcquisitionRevoked,
                ReleaseAccessDetached,
            ),
        ):
            return release
        raise TypeError("unsupported shared lifecycle release decision")


__all__ = [
    "AdbTransportListWatchAcquireError",
    "AdbTransportListWatchAcquireInterruptedError",
    "AdbTransportListWatchLifecycleTemplate",
]
