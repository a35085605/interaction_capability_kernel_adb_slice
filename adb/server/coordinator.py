from __future__ import annotations

from typing import Generic, TypeVar

from _lifecycle_new.capability.coordinator import CapabilityLifecycleCoordinator
from _lifecycle_new.resource.contract import ResourceProvider
from _lifecycle_new.resource.driver import PhysicalResources
from adb.server.capability import AdbServerCapability
from adb.server.generation import AdbServerGeneration, AdbServerGenerationIssuer
from adb.server.lifecycle import AdbServerAcquireResult, AdbServerReleaseResult
from adb.server.request import AdbServerRequest
from adb.server.state import AdbServerSnapshot


PhysicalResourceT = TypeVar("PhysicalResourceT")


class _AdbServerCapabilityProjector(Generic[PhysicalResourceT]):
    """Project the public server capability while keeping physical resources private."""

    def project(
        self,
        request: AdbServerRequest,
        resources: PhysicalResources[PhysicalResourceT],
    ) -> AdbServerCapability:
        return AdbServerCapability(request.server_address)


class AdbServerLifecycleCoordinator(Generic[PhysicalResourceT]):
    """Specialize the simplified synchronous lifecycle for one ADB server."""

    def __init__(
        self,
        generation_issuer: AdbServerGenerationIssuer,
        resource_provider: ResourceProvider[AdbServerRequest, PhysicalResourceT],
    ) -> None:
        if not isinstance(generation_issuer, AdbServerGenerationIssuer):
            raise TypeError("generation_issuer must be AdbServerGenerationIssuer")
        if not callable(getattr(resource_provider, "acquire", None)):
            raise TypeError("resource_provider must provide acquire()")
        if not callable(getattr(resource_provider, "release", None)):
            raise TypeError("resource_provider must provide release()")

        self._generation_issuer = generation_issuer
        self._resource_provider = resource_provider
        self._coordinator: CapabilityLifecycleCoordinator[
            AdbServerGeneration,
            AdbServerRequest,
            PhysicalResourceT,
            AdbServerCapability,
        ] = CapabilityLifecycleCoordinator(
            generation_issuer.issue,
            resource_provider,
            _AdbServerCapabilityProjector(),
        )

    @property
    def generation_issuer(self) -> AdbServerGenerationIssuer:
        return self._generation_issuer

    @property
    def resource_provider(self) -> ResourceProvider[AdbServerRequest, PhysicalResourceT]:
        return self._resource_provider

    def read(self) -> AdbServerSnapshot:
        return self._coordinator.read()

    def acquire(
        self,
        expected_generation: AdbServerGeneration,
        request: AdbServerRequest,
    ) -> AdbServerAcquireResult:
        if not isinstance(expected_generation, AdbServerGeneration):
            raise TypeError("expected_generation must be AdbServerGeneration")
        if not isinstance(request, AdbServerRequest):
            raise TypeError("request must be AdbServerRequest")
        return self._coordinator.acquire(expected_generation, request)

    def release(
        self,
        expected_generation: AdbServerGeneration,
        request: AdbServerRequest,
    ) -> AdbServerReleaseResult:
        if not isinstance(expected_generation, AdbServerGeneration):
            raise TypeError("expected_generation must be AdbServerGeneration")
        if not isinstance(request, AdbServerRequest):
            raise TypeError("request must be AdbServerRequest")
        return self._coordinator.release(expected_generation, request)


__all__ = ["AdbServerLifecycleCoordinator"]
