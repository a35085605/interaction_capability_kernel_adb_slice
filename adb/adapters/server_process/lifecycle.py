from __future__ import annotations

from typing import Generic, TypeVar

from lifecycle.resource.driver import (
    PhysicalResources,
    RequirementAcquireFailed,
    RequirementAcquireResult,
    ResourceDriver,
)
from lifecycle.resource.provider import ResolvedResourceProvider
from adb.adapters.server_process.errors import AospAdbServerStartError
from adb.server.coordinator import AdbServerLifecycleCoordinator
from adb.server.error import AdbServerAcquireError
from adb.server.generation import AdbServerGenerationIssuer
from adb.server.request import AdbServerRequest
from networking import TcpEndpoint


PhysicalResourceT = TypeVar("PhysicalResourceT")


class _AdbServerSubprocessRequirementsResolver:
    """Resolve one server-domain request into its AOSP process requirement."""

    def resolve(self, request: AdbServerRequest) -> tuple[TcpEndpoint, ...]:
        if not isinstance(request, AdbServerRequest):
            raise TypeError("request must be AdbServerRequest")
        return (request.server_endpoint,)


class _AdbServerDomainDriver(Generic[PhysicalResourceT]):
    """Translate platform process-driver failures into server-domain acquisition errors."""

    def __init__(self, driver: ResourceDriver[TcpEndpoint, PhysicalResourceT]) -> None:
        if not callable(getattr(driver, "acquire", None)):
            raise TypeError("driver must provide acquire()")
        if not callable(getattr(driver, "cleanup", None)):
            raise TypeError("driver must provide cleanup()")
        self._driver = driver

    def acquire(
        self,
        server_endpoint: TcpEndpoint,
    ) -> RequirementAcquireResult[PhysicalResourceT]:
        outcome = self._driver.acquire(server_endpoint)
        if (
            isinstance(outcome, RequirementAcquireFailed)
            and isinstance(outcome.error, AospAdbServerStartError)
        ):
            return RequirementAcquireFailed(
                AdbServerAcquireError(str(outcome.error)),
                outcome.resources,
            )
        return outcome

    def cleanup(self, resources: PhysicalResources[PhysicalResourceT]) -> None:
        self._driver.cleanup(resources)


class AdbServerProcessLifecycle(
    AdbServerLifecycleCoordinator[PhysicalResourceT],
    Generic[PhysicalResourceT],
):
    """Adapt one explicitly supplied server-process driver into the server lifecycle.

    Platform selection and driver construction intentionally live in the runtime
    composition root. This adapter only translates the process driver's requirement
    and failure model into the server-domain lifecycle contract.
    """

    def __init__(
        self,
        generation_issuer: AdbServerGenerationIssuer,
        driver: ResourceDriver[TcpEndpoint, PhysicalResourceT],
    ) -> None:
        if not callable(getattr(driver, "acquire", None)):
            raise TypeError("driver must provide acquire()")
        if not callable(getattr(driver, "cleanup", None)):
            raise TypeError("driver must provide cleanup()")
        self._driver = driver
        resource_provider = ResolvedResourceProvider(
            _AdbServerSubprocessRequirementsResolver(),
            _AdbServerDomainDriver(driver),
        )
        super().__init__(generation_issuer, resource_provider)

    @property
    def driver(self) -> ResourceDriver[TcpEndpoint, PhysicalResourceT]:
        return self._driver


__all__ = ["AdbServerProcessLifecycle"]
