from __future__ import annotations

from dataclasses import dataclass

from adb.server.endpoint import AdbServerEndpoint
from adb.server.generation import AdbServerGeneration
from adb.server.lifecycle.backend import AdbServerBackendAcquired


@dataclass(frozen=True, slots=True)
class AdbServerActivated:
    """Lifecycle evidence that a backend acquisition became available to the runtime."""

    acquisition: AdbServerBackendAcquired

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition, AdbServerBackendAcquired):
            raise TypeError("acquisition must be AdbServerBackendAcquired")

    @property
    def server(self) -> AdbServerGeneration:
        return self.acquisition.generation

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self.acquisition.endpoint


@dataclass(frozen=True, slots=True)
class AdbServerDeactivated:
    """Lifecycle evidence that a backend acquisition was released by the runtime."""

    acquisition: AdbServerBackendAcquired

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition, AdbServerBackendAcquired):
            raise TypeError("acquisition must be AdbServerBackendAcquired")

    @property
    def server(self) -> AdbServerGeneration:
        return self.acquisition.generation

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self.acquisition.endpoint


__all__ = ["AdbServerActivated", "AdbServerDeactivated"]
