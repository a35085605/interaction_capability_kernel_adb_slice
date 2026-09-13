from __future__ import annotations

from typing import Generic, TypeVar

from networking import TcpAddress

from _lifecycle_new.capability.coordinator import CapabilityLifecycleCoordinator
from _lifecycle_new.resource.contract import ResourceProvider
from _lifecycle_new.resource.driver import PhysicalResources
from adb.server.access import AdbServerAccess
from adb.server.generation import AdbServerGeneration, AdbServerGenerationIssuer
from adb.server.lifecycle import (
    AdbServerAcquireResult,
    AdbServerReleaseResult,
)
from adb.server.state import AdbServerLifecycleSnapshot


PhysicalResourceT = TypeVar("PhysicalResourceT")


class _AdbServerCapabilityProjector(Generic[PhysicalResourceT]):
    """Project public ADB server access from an acquired server request.

    Physical server resources stay lifecycle-private. Once acquisition succeeds,
    the public capability remains the requested server address.
    """

    def project(
        self,
        request: AdbServerAccess,
        resources: PhysicalResources[PhysicalResourceT],
    ) -> TcpAddress:
        return request.server_address


class AdbServerLifecycleCoordinator(Generic[PhysicalResourceT]):
    """ADB-server specialization of the simplified synchronous lifecycle.

    The injected resource provider owns all physical I/O. This coordinator only
    fences requests by generation, retains resources until explicit release, and
    projects the successful request into the public ``TcpAddress`` capability.

    Acquisition and release are deliberately non-cancellable lifecycle operations.
    Calls that overlap in-flight acquire/release work receive ``LifecycleBusy`` from
    the shared coordinator rather than revoking or draining the operation.
    """

    def __init__(
        self,
        generation_issuer: AdbServerGenerationIssuer,
        resource_provider: ResourceProvider[AdbServerAccess, PhysicalResourceT],
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
            AdbServerAccess,
            PhysicalResourceT,
            TcpAddress,
        ] = CapabilityLifecycleCoordinator(
            generation_issuer.issue,
            resource_provider,
            _AdbServerCapabilityProjector(),
        )

    @property
    def generation_issuer(self) -> AdbServerGenerationIssuer:
        return self._generation_issuer

    @property
    def resource_provider(self) -> ResourceProvider[AdbServerAccess, PhysicalResourceT]:
        return self._resource_provider

    def read(self) -> AdbServerLifecycleSnapshot:
        return self._coordinator.read()

    def acquire(
        self,
        expected_generation: AdbServerGeneration,
        access: AdbServerAccess,
    ) -> AdbServerAcquireResult:
        if not isinstance(expected_generation, AdbServerGeneration):
            raise TypeError("expected_generation must be AdbServerGeneration")
        if not isinstance(access, AdbServerAccess):
            raise TypeError("access must be AdbServerAccess")
        return self._coordinator.acquire(expected_generation, access)

    def release(
        self,
        expected_generation: AdbServerGeneration,
        access: AdbServerAccess,
    ) -> AdbServerReleaseResult:
        if not isinstance(expected_generation, AdbServerGeneration):
            raise TypeError("expected_generation must be AdbServerGeneration")
        if not isinstance(access, AdbServerAccess):
            raise TypeError("access must be AdbServerAccess")
        return self._coordinator.release(expected_generation, access)


__all__ = ["AdbServerLifecycleCoordinator"]
