from __future__ import annotations

import os
from typing import Any

from lifecycle.resource.driver import (
    PhysicalResources,
    RequirementAcquireFailed,
    RequirementAcquireResult,
)
from lifecycle.resource.manager import ResolvedResourceProvider
from adb.adapters.server_process.posix import (
    AospAdbServerProcessDriver,
    AospAdbServerStartError,
)
from adb.server.coordinator import AdbServerLifecycleCoordinator
from adb.server.error import AdbServerAcquireError
from adb.server.generation import AdbServerGenerationIssuer
from adb.server.request import AdbServerRequest
from networking import TcpEndpoint


class _AdbServerSubprocessRequirementsResolver:
    """Resolve one server-domain request into its AOSP process requirement."""

    def resolve(self, request: AdbServerRequest) -> tuple[TcpEndpoint, ...]:
        if not isinstance(request, AdbServerRequest):
            raise TypeError("request must be AdbServerRequest")
        return (request.server_endpoint,)


class _AdbServerDomainDriver:
    """Translate AOSP server-process failures into server-domain acquisition errors."""

    def __init__(self, driver: Any) -> None:
        if not callable(getattr(driver, "acquire", None)):
            raise TypeError("driver must provide acquire()")
        if not callable(getattr(driver, "cleanup", None)):
            raise TypeError("driver must provide cleanup()")
        self._driver = driver

    def acquire(self, server_endpoint: TcpEndpoint) -> RequirementAcquireResult[Any]:
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

    def cleanup(self, resources: PhysicalResources[Any]) -> None:
        self._driver.cleanup(resources)


class AdbServerProcessLifecycle(AdbServerLifecycleCoordinator[Any]):
    """Adapt an owned AOSP ADB server process into the server capability lifecycle."""

    def __init__(
        self,
        generation_issuer: AdbServerGenerationIssuer,
        *,
        executable: str = "adb",
        startup_timeout_seconds: float = 5.0,
        shutdown_timeout_seconds: float = 5.0,
        probe_interval_seconds: float = 0.05,
        _factory: Any | None = None,
    ) -> None:
        if _factory is None:
            if os.name == "nt":
                from adb.adapters.server_process.windows import (
                    WindowsAospAdbServerProcessDriver,
                )

                _factory = WindowsAospAdbServerProcessDriver(
                    executable=executable,
                    startup_timeout_seconds=startup_timeout_seconds,
                    shutdown_timeout_seconds=shutdown_timeout_seconds,
                    probe_interval_seconds=probe_interval_seconds,
                )
            else:
                _factory = AospAdbServerProcessDriver(
                    executable=executable,
                    startup_timeout_seconds=startup_timeout_seconds,
                    shutdown_timeout_seconds=shutdown_timeout_seconds,
                    probe_interval_seconds=probe_interval_seconds,
                )

        self._factory = _factory
        resource_provider = ResolvedResourceProvider(
            _AdbServerSubprocessRequirementsResolver(),
            _AdbServerDomainDriver(_factory),
        )
        super().__init__(generation_issuer, resource_provider)


__all__ = ["AdbServerProcessLifecycle"]
