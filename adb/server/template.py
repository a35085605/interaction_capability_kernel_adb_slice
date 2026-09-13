from __future__ import annotations

from typing import Generic, TypeVar

from _lifecycle_new.resource.contract import ResourceProvider
from adb.server.coordinator import AdbServerLifecycleCoordinator
from adb.server.generation import AdbServerGeneration, AdbServerGenerationIssuer
from adb.server.lifecycle import AdbServerAcquireResult, AdbServerReleaseResult
from adb.server.request import AdbServerRequest
from adb.server.state import AdbServerSnapshot


PhysicalResourceT = TypeVar("PhysicalResourceT")


def _normalize_diagnostic(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("diagnostic must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("diagnostic cannot be empty")
    return normalized


class AdbServerAcquireError(RuntimeError):
    """Expected failure while obtaining usable ADB server access."""

    def __init__(self, diagnostic: str) -> None:
        self.diagnostic = _normalize_diagnostic(diagnostic)
        super().__init__(self.diagnostic)


class AdbServerLifecycleTemplate(Generic[PhysicalResourceT]):
    """Bind ADB-server lifecycle semantics to one synchronous resource provider."""

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

        self._coordinator = AdbServerLifecycleCoordinator(
            generation_issuer,
            resource_provider,
        )

    @property
    def coordinator(self) -> AdbServerLifecycleCoordinator[PhysicalResourceT]:
        return self._coordinator

    def read(self) -> AdbServerSnapshot:
        return self._coordinator.read()

    def acquire(
        self,
        expected_generation: AdbServerGeneration,
        request: AdbServerRequest,
    ) -> AdbServerAcquireResult:
        return self._coordinator.acquire(expected_generation, request)

    def release(
        self,
        expected_generation: AdbServerGeneration,
        request: AdbServerRequest,
    ) -> AdbServerReleaseResult:
        return self._coordinator.release(expected_generation, request)


__all__ = [
    "AdbServerAcquireError",
    "AdbServerLifecycleTemplate",
]
