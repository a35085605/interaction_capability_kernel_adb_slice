from __future__ import annotations

from typing import Generic, TypeVar

from _lifecycle_new.capability.projection import CapabilityProjector
from _lifecycle_new.resource.contract import ResourceProvider
from adb.transport_list.watch.coordinator import AdbTransportListWatchLifecycleCoordinator
from adb.transport_list.watch.failure import AdbTransportListWatchFailure
from adb.transport_list.watch.generation import (
    AdbTransportListWatchGeneration,
    AdbTransportListWatchGenerationIssuer,
)
from adb.transport_list.watch.lifecycle import (
    AdbTransportListWatchAcquireResult,
    AdbTransportListWatchReleaseResult,
)
from adb.transport_list.watch.request import AdbTransportListWatchRequest
from adb.transport_list.watch.state import AdbTransportListWatchState
from adb.transport_list.watch.stream import AdbTransportListWatchStream


PhysicalResourceT = TypeVar("PhysicalResourceT")


class AdbTransportListWatchAcquireError(RuntimeError):
    """Expected failure while establishing a usable transport-list watch."""

    def __init__(self, failure: AdbTransportListWatchFailure) -> None:
        if not isinstance(failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")
        self.failure = failure
        super().__init__(failure.diagnostic or type(failure).__name__)


class AdbTransportListWatchLifecycleTemplate(Generic[PhysicalResourceT]):
    """Bind watch lifecycle semantics to synchronous resources and capability projection."""

    def __init__(
        self,
        generation_issuer: AdbTransportListWatchGenerationIssuer,
        resource_provider: ResourceProvider[AdbTransportListWatchRequest, PhysicalResourceT],
        capability_projector: CapabilityProjector[
            AdbTransportListWatchRequest,
            PhysicalResourceT,
            AdbTransportListWatchStream,
        ],
    ) -> None:
        self._coordinator = AdbTransportListWatchLifecycleCoordinator(
            generation_issuer,
            resource_provider,
            capability_projector,
        )

    @property
    def coordinator(self) -> AdbTransportListWatchLifecycleCoordinator[PhysicalResourceT]:
        return self._coordinator

    def read(self) -> AdbTransportListWatchState:
        return self._coordinator.read()

    def acquire(
        self,
        expected_generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
    ) -> AdbTransportListWatchAcquireResult:
        return self._coordinator.acquire(expected_generation, request)

    def release(
        self,
        expected_generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
    ) -> AdbTransportListWatchReleaseResult:
        return self._coordinator.release(expected_generation, request)


__all__ = [
    "AdbTransportListWatchAcquireError",
    "AdbTransportListWatchLifecycleTemplate",
]
