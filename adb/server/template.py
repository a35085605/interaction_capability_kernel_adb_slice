from __future__ import annotations

from typing import Generic, TypeVar

from _lifecycle_new.resource.contract import ResourceProvider
from adb.server.access import AdbServerAccess
from adb.server.coordinator import AdbServerLifecycleCoordinator
from adb.server.generation import AdbServerGeneration, AdbServerGenerationIssuer
from adb.server.lifecycle import AdbServerAcquireResult, AdbServerReleaseResult
from adb.server.state import AdbServerLifecycleSnapshot


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
    """Bind ADB-server lifecycle semantics to one synchronous resource provider.

    The provider owns all physical I/O and cleanup. Lifecycle coordination is delegated to
    ``AdbServerLifecycleCoordinator``: acquire/release operations are synchronous, overlapping
    lifecycle calls report ``LifecycleBusy``, and failed acquisition or cleanup remains in
    ``RELEASE_REQUIRED`` until an explicit matching release succeeds.

    In particular, this template has no cancellation, draining, global resource pool, resource
    claims, or background cleanup path. Those mechanisms belonged to the retired lifecycle model.
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

        self._coordinator = AdbServerLifecycleCoordinator(
            generation_issuer,
            resource_provider,
        )

    @property
    def coordinator(self) -> AdbServerLifecycleCoordinator[PhysicalResourceT]:
        return self._coordinator

    def read(self) -> AdbServerLifecycleSnapshot:
        return self._coordinator.read()

    def acquire(
        self,
        expected_generation: AdbServerGeneration,
        access: AdbServerAccess,
    ) -> AdbServerAcquireResult:
        return self._coordinator.acquire(expected_generation, access)

    def release(
        self,
        expected_generation: AdbServerGeneration,
        access: AdbServerAccess,
    ) -> AdbServerReleaseResult:
        return self._coordinator.release(expected_generation, access)


__all__ = [
    "AdbServerAcquireError",
    "AdbServerLifecycleTemplate",
]
