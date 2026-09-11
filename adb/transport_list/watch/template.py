from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from threading import Event
from typing import Protocol

from networking import TcpAddress
from adb._lifecycle import (
    GLOBAL_RESOURCE_POOL,
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
    ManagedLifecycle,
    ReleaseAccessDetached,
    ReleaseAccessMismatch,
    ReleaseAcquisitionRevoked,
    ReleaseInactive,
    ResourcePool,
    ResourceScope,
)
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
    """Lifecycle-private view over a pool-registered physical watch resource."""


class _AdbTransportListWatchStreamView:
    """Narrow producer capability over a lifecycle-owned watch resource."""

    __slots__ = ("__resource",)

    def __init__(self, resource: _AdbTransportListWatchResource) -> None:
        self.__resource = resource

    @property
    def initial(self) -> AdbTransportList:
        return self.__resource.initial

    def updates(self) -> Iterator[AdbTransportList]:
        return self.__resource.updates()


def _superseded_after_abandon(
    result: AcquireAbandonResult[AdbTransportListWatchGeneration],
) -> AcquireSuperseded[AdbTransportListWatchGeneration] | None:
    if isinstance(result, AcquireAttemptRevoked):
        return AcquireSuperseded(result.current_generation)
    if isinstance(result, AcquireAttemptAbandoned):
        return None
    raise TypeError("unsupported shared lifecycle acquire abandonment")


class AdbTransportListWatchLifecycleTemplate(ABC):
    """Template for one current watch generation backed by the shared resource pool."""

    def __init__(
        self,
        generation_issuer: AdbTransportListWatchGenerationIssuer,
        *,
        _resource_pool: ResourcePool = GLOBAL_RESOURCE_POOL,
    ) -> None:
        if not isinstance(generation_issuer, AdbTransportListWatchGenerationIssuer):
            raise TypeError(
                "generation_issuer must be AdbTransportListWatchGenerationIssuer"
            )
        if not isinstance(_resource_pool, ResourcePool):
            raise TypeError("_resource_pool must be ResourcePool")
        self._managed: ManagedLifecycle[
            AdbTransportListWatchGeneration,
            AdbTransportListWatchAccess,
            AdbTransportListWatchStream,
        ] = ManagedLifecycle(
            generation_issuer.issue,
            resource_pool=_resource_pool,
        )

    def read(self) -> AdbTransportListWatchState:
        return self._managed.read()

    @abstractmethod
    def _obtain_resource(
        self,
        server_address: TcpAddress,
        cancellation: Event,
        resources: ResourceScope,
    ) -> _AdbTransportListWatchResource:
        """Obtain a usable watch while registering produced physical resources immediately."""

    def acquire(
        self,
        expected_generation: AdbTransportListWatchGeneration,
        access: AdbTransportListWatchAccess,
    ) -> AdbTransportListWatchAcquireOutcome:
        if not isinstance(expected_generation, AdbTransportListWatchGeneration):
            raise TypeError("expected_generation must be AdbTransportListWatchGeneration")
        if not isinstance(access, AdbTransportListWatchAccess):
            raise TypeError("access must be AdbTransportListWatchAccess")
        return self._acquire(expected_generation, access)

    def _acquire(
        self,
        expected_generation: AdbTransportListWatchGeneration,
        access: AdbTransportListWatchAccess,
    ) -> AdbTransportListWatchAcquireOutcome:
        server_address = access.server_address
        start = self._managed.begin_acquire(expected_generation, access)
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
            except AdbTransportListWatchAcquireInterruptedError as exc:
                superseded = _superseded_after_abandon(attempt.abandon())
                if superseded is not None:
                    return superseded
                raise RuntimeError(
                    "ADB transport-list watch acquisition was interrupted without generation "
                    "revocation"
                ) from exc
            except AdbTransportListWatchAcquireError as exc:
                superseded = _superseded_after_abandon(attempt.abandon())
                if superseded is not None:
                    return superseded
                return AcquireFailed(exc.failure)

            capability = _AdbTransportListWatchStreamView(resource)
            finalized = attempt.commit(capability=capability)
            if isinstance(finalized, AcquireAttemptCommitted):
                return AcquireCommitted(finalized.snapshot)
            if isinstance(finalized, AcquireAttemptRevoked):
                return AcquireSuperseded(finalized.current_generation)
            raise TypeError("unsupported shared lifecycle acquire commit")

    def release(
        self,
        expected_generation: AdbTransportListWatchGeneration,
        access: AdbTransportListWatchAccess,
    ) -> AdbTransportListWatchReleaseOutcome:
        if not isinstance(expected_generation, AdbTransportListWatchGeneration):
            raise TypeError("expected_generation must be AdbTransportListWatchGeneration")
        if not isinstance(access, AdbTransportListWatchAccess):
            raise TypeError("access must be AdbTransportListWatchAccess")
        return self._release(expected_generation, access)

    def _release(
        self,
        expected_generation: AdbTransportListWatchGeneration,
        access: AdbTransportListWatchAccess,
    ) -> AdbTransportListWatchReleaseOutcome:
        release = self._managed.release(
            expected_generation,
            access,
            inconsistent_state_error=(
                "ADB transport-list watch lifecycle state is inconsistent"
            ),
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
    "AdbTransportListWatchAcquireError",
    "AdbTransportListWatchAcquireInterruptedError",
    "AdbTransportListWatchLifecycleTemplate",
]
