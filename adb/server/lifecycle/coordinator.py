from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import TypeAlias

from eventing import EventPublisher
from networking import TcpAddress

from adb.server.endpoint import AdbServerEndpoint
from adb.server.generation import AdbServerGeneration
from adb.server.state import AdbServerState
from adb.server.lifecycle.backend import (
    AdbServerBackend,
    AdbServerBackendAcquired,
    AdbServerBackendAcquireDeferred,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireRevoked,
    AdbServerBackendAlreadyAcquired,
    AdbServerBackendAcquireResult,
    AdbServerBackendReleased,
    AdbServerBackendReleaseInactive,
    AdbServerBackendReleaseMismatch,
)
from adb.server.lifecycle.errors import AdbServerLifecycleConsistencyError
from adb.server.lifecycle.events import AdbServerActivated, AdbServerDeactivated


@dataclass(frozen=True, slots=True)
class AdbServerAlreadyActive:
    """Evidence that provision found the backend's current authoritative acquisition."""

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
class AdbServerAlreadyInactive:
    """Evidence that retirement found no authority in the matching current generation."""


@dataclass(frozen=True, slots=True)
class AdbServerRetired:
    """Evidence that matching generation was retired before activation committed."""

    server: AdbServerGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerGeneration):
            raise TypeError("server must be AdbServerGeneration")


AdbServerProvisionResult: TypeAlias = (
    tuple[AdbServerAlreadyActive]
    | tuple[
        AdbServerBackendAcquireDeferred
        | AdbServerBackendAcquireFailed
        | AdbServerBackendAcquireRevoked
    ]
    | tuple[AdbServerBackendAcquired, AdbServerActivated]
)
AdbServerRetireResult: TypeAlias = (
    AdbServerAlreadyInactive
    | AdbServerRetired
    | AdbServerBackendReleaseMismatch
    | AdbServerDeactivated
)


class AdbServerLifecycleCoordinator:
    """Coordinate backend generation authority with lifecycle evidence and publication.

    The backend is the sole authority for the current server generation. The coordinator
    adds endpoint-constraint orchestration and lifecycle event publication only.
    """

    def __init__(
        self,
        backend: AdbServerBackend,
        *,
        endpoint_constraint: AdbServerEndpoint | None,
        publisher: EventPublisher | None = None,
    ) -> None:
        if not isinstance(backend, AdbServerBackend):
            raise TypeError("backend must satisfy AdbServerBackend")
        if endpoint_constraint is not None and not isinstance(endpoint_constraint, TcpAddress):
            raise TypeError("endpoint_constraint must be TcpAddress or None")
        if publisher is not None and not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher or be None")
        self._backend = backend
        self._endpoint_constraint = endpoint_constraint
        self._publisher = publisher
        self._lock = RLock()

    def read(self) -> AdbServerState:
        """Return the backend's authoritative atomic server-state snapshot."""

        return self._backend.read()

    def provision(self) -> AdbServerProvisionResult:
        """Ensure usable server access and publish activation for a new acquisition."""

        with self._lock:
            endpoint_constraint = self._endpoint_constraint

        acquisition = self._acquire_backend(endpoint_constraint)
        if isinstance(acquisition, AdbServerBackendAlreadyAcquired):
            return (AdbServerAlreadyActive(acquisition.acquisition),)
        if isinstance(
            acquisition,
            (
                AdbServerBackendAcquireDeferred,
                AdbServerBackendAcquireFailed,
                AdbServerBackendAcquireRevoked,
            ),
        ):
            return (acquisition,)
        if not isinstance(acquisition, AdbServerBackendAcquired):
            raise TypeError("server backend acquire() returned an unsupported result")

        activation = AdbServerActivated(acquisition)
        if self._publisher is not None:
            self._publisher.publish(activation)
        return (acquisition, activation)

    def _acquire_backend(
        self,
        endpoint_constraint: AdbServerEndpoint | None,
    ) -> AdbServerBackendAcquireResult:
        acquisition = self._backend.acquire(endpoint_constraint)
        if isinstance(
            acquisition,
            (
                AdbServerBackendAlreadyAcquired,
                AdbServerBackendAcquireDeferred,
                AdbServerBackendAcquireFailed,
                AdbServerBackendAcquireRevoked,
            ),
        ):
            return acquisition
        if not isinstance(acquisition, AdbServerBackendAcquired):
            raise TypeError("server backend acquire() returned an unsupported result")

        if endpoint_constraint is not None and acquisition.endpoint != endpoint_constraint:
            self._backend.release(acquisition.generation)
            raise AdbServerLifecycleConsistencyError(
                "endpoint-constrained ADB server backend acquisition returned a different endpoint"
            )
        return acquisition

    def retire(
        self,
        *,
        expected_server: AdbServerGeneration | None = None,
    ) -> AdbServerRetireResult:
        """Release current authority, fenced by optional server generation.

        The backend always has a generation, including while idle. Unfenced retirement snapshots
        that generation and lets ``release`` atomically distinguish inactivity from pending or
        usable authority. Pending acquisition is revocable before an endpoint exists.
        """

        if expected_server is not None and not isinstance(expected_server, AdbServerGeneration):
            raise TypeError("expected_server must be AdbServerGeneration or None")

        expected_generation = (
            self._backend.read().generation if expected_server is None else expected_server
        )
        release = self._backend.release(expected_generation)
        if isinstance(release, AdbServerBackendReleaseInactive):
            return AdbServerAlreadyInactive()
        if isinstance(release, AdbServerBackendReleaseMismatch):
            return release
        if not isinstance(release, AdbServerBackendReleased):
            raise TypeError("server backend release() returned an unsupported result")
        if release.acquisition is None:
            return AdbServerRetired(release.generation)

        deactivation = AdbServerDeactivated(release.acquisition)
        if self._publisher is not None:
            self._publisher.publish(deactivation)
        return deactivation

    def configure_endpoint_constraint(self, endpoint_constraint: AdbServerEndpoint | None) -> None:
        """Replace the endpoint constraint captured by subsequent acquisition attempts."""

        if endpoint_constraint is not None and not isinstance(endpoint_constraint, TcpAddress):
            raise TypeError("endpoint_constraint must be TcpAddress or None")
        with self._lock:
            self._endpoint_constraint = endpoint_constraint


__all__ = [
    "AdbServerAlreadyActive",
    "AdbServerAlreadyInactive",
    "AdbServerLifecycleCoordinator",
    "AdbServerProvisionResult",
    "AdbServerRetired",
    "AdbServerRetireResult",
]
