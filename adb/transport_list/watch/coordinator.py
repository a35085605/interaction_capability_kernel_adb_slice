from __future__ import annotations

from typing import Generic, TypeVar

from lifecycle.capability.coordinator import CapabilityLifecycleCoordinator
from lifecycle.capability.projection import CapabilityProjector
from lifecycle.resource.contract import ResourceProvider
from adb.transport_list.watch.generation import (
    AdbTransportListWatchGeneration,
    AdbTransportListWatchGenerationIssuer,
)
from adb.transport_list.watch.lifecycle import (
    AdbTransportListWatchAcquireResult,
    AdbTransportListWatchReleaseResult,
)
from adb.transport_list.watch.request import AdbTransportListWatchRequest
from adb.transport_list.watch.snapshot import AdbTransportListWatchSnapshot
from adb.transport_list.watch.stream import AdbTransportListWatchStream


PhysicalResourceT = TypeVar("PhysicalResourceT")


class AdbTransportListWatchLifecycleCoordinator(Generic[PhysicalResourceT]):
    """Specialize the simplified synchronous lifecycle for one transport-list watch."""

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
        if not isinstance(generation_issuer, AdbTransportListWatchGenerationIssuer):
            raise TypeError(
                "generation_issuer must be AdbTransportListWatchGenerationIssuer"
            )
        if not callable(getattr(resource_provider, "acquire", None)):
            raise TypeError("resource_provider must provide acquire()")
        if not callable(getattr(resource_provider, "release", None)):
            raise TypeError("resource_provider must provide release()")
        if not callable(getattr(capability_projector, "project", None)):
            raise TypeError("capability_projector must provide project()")

        self._generation_issuer = generation_issuer
        self._resource_provider = resource_provider
        self._capability_projector = capability_projector
        self._coordinator: CapabilityLifecycleCoordinator[
            AdbTransportListWatchGeneration,
            AdbTransportListWatchRequest,
            PhysicalResourceT,
            AdbTransportListWatchStream,
        ] = CapabilityLifecycleCoordinator(
            generation_issuer.issue,
            resource_provider,
            capability_projector,
        )

    @property
    def generation_issuer(self) -> AdbTransportListWatchGenerationIssuer:
        return self._generation_issuer

    @property
    def resource_provider(
        self,
    ) -> ResourceProvider[AdbTransportListWatchRequest, PhysicalResourceT]:
        return self._resource_provider

    def read(self) -> AdbTransportListWatchSnapshot:
        return self._coordinator.read()

    def acquire(
        self,
        expected_generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
    ) -> AdbTransportListWatchAcquireResult:
        if not isinstance(expected_generation, AdbTransportListWatchGeneration):
            raise TypeError("expected_generation must be AdbTransportListWatchGeneration")
        if not isinstance(request, AdbTransportListWatchRequest):
            raise TypeError("request must be AdbTransportListWatchRequest")
        return self._coordinator.acquire(expected_generation, request)

    def release(
        self,
        expected_generation: AdbTransportListWatchGeneration,
        request: AdbTransportListWatchRequest,
    ) -> AdbTransportListWatchReleaseResult:
        if not isinstance(expected_generation, AdbTransportListWatchGeneration):
            raise TypeError("expected_generation must be AdbTransportListWatchGeneration")
        if not isinstance(request, AdbTransportListWatchRequest):
            raise TypeError("request must be AdbTransportListWatchRequest")
        return self._coordinator.release(expected_generation, request)


__all__ = ["AdbTransportListWatchLifecycleCoordinator"]
