from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import TypeAlias

from eventing import EventPublisher
from networking import TcpAddress

from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity
from adb.server.lifecycle.backend import (
    AdbServerBackend,
    AdbServerBackendAcquired,
    AdbServerBackendAcquireDeferred,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAlreadyAcquired,
    AdbServerBackendAcquireResult,
    AdbServerBackendReleased,
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
    def server(self) -> AdbServerIdentity:
        return self.acquisition.identity

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self.acquisition.endpoint


@dataclass(frozen=True, slots=True)
class AdbServerAlreadyInactive:
    """Evidence that unfenced retirement found no current backend acquisition."""


AdbServerProvisionResult: TypeAlias = (
    tuple[AdbServerAlreadyActive]
    | tuple[AdbServerBackendAcquireDeferred | AdbServerBackendAcquireFailed]
    | tuple[AdbServerBackendAcquired, AdbServerActivated]
)
AdbServerRetireResult: TypeAlias = (
    AdbServerAlreadyInactive | AdbServerBackendReleaseMismatch | AdbServerDeactivated
)


class AdbServerLifecycleCoordinator:
    """Coordinate backend ownership with lifecycle evidence and publication.

    The backend is the sole authority for the current server acquisition. The coordinator
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

    def provision(self) -> AdbServerProvisionResult:
        """Ensure usable server access and publish activation for a new acquisition."""

        with self._lock:
            endpoint_constraint = self._endpoint_constraint

        acquisition = self._acquire_backend(endpoint_constraint)
        if isinstance(acquisition, AdbServerBackendAlreadyAcquired):
            return (AdbServerAlreadyActive(acquisition.acquisition),)
        if isinstance(
            acquisition,
            (AdbServerBackendAcquireDeferred, AdbServerBackendAcquireFailed),
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
            ),
        ):
            return acquisition
        if not isinstance(acquisition, AdbServerBackendAcquired):
            raise TypeError("server backend acquire() returned an unsupported result")

        if endpoint_constraint is not None and acquisition.endpoint != endpoint_constraint:
            self._backend.release(acquisition.identity)
            raise AdbServerLifecycleConsistencyError(
                "endpoint-constrained ADB server backend acquisition returned a different endpoint"
            )
        return acquisition

    def retire(
        self,
        *,
        expected_server: AdbServerIdentity | None = None,
    ) -> AdbServerRetireResult:
        """Release the current backend acquisition, fenced by optional server identity."""

        if expected_server is not None and not isinstance(expected_server, AdbServerIdentity):
            raise TypeError("expected_server must be AdbServerIdentity or None")

        if expected_server is None:
            current = self._backend.current
            if current is None:
                return AdbServerAlreadyInactive()
            expected_server = current.identity

        release = self._backend.release(expected_server)
        if isinstance(release, AdbServerBackendReleaseMismatch):
            return release
        if not isinstance(release, AdbServerBackendReleased):
            raise TypeError("server backend release() returned an unsupported result")

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
    "AdbServerRetireResult",
]
