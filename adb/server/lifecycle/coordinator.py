from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import TypeAlias

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity
from adb.server.lifecycle.backend import (
    AdbServerBackend,
    AdbServerBackendAcquireBlocked,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireInProgress,
    AdbServerBackendAcquireResult,
    AdbServerBackendAcquireSatisfied,
    AdbServerBackendAcquireSucceeded,
)
from adb.server.lifecycle.errors import AdbServerLifecycleConsistencyError
from adb.server.state import (
    AdbServerActivated,
    AdbServerActivationResult,
    AdbServerActivationStateConflict,
    AdbServerDeactivated,
    AdbServerDeactivationResult,
    AdbServerDeactivationStateConflict,
    AdbServerState,
    AdbServerStateStore,
)


@dataclass(frozen=True, slots=True)
class AdbServerAlreadyActive:
    """Evidence that provision linearized against an already-active authoritative server."""

    server: AdbServerIdentity
    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


@dataclass(frozen=True, slots=True)
class AdbServerAlreadyInactive:
    """Evidence that unfenced retirement found no active authoritative server."""

    state: AdbServerState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AdbServerState):
            raise TypeError("state must be AdbServerState")
        if self.state.active:
            raise ValueError("already-inactive result requires inactive server state")


AdbServerProvisionResult: TypeAlias = (
    tuple[AdbServerAlreadyActive]
    | tuple[
        AdbServerBackendAcquireInProgress
        | AdbServerBackendAcquireBlocked
        | AdbServerBackendAcquireFailed
    ]
    | tuple[
        AdbServerBackendAcquireSucceeded | AdbServerBackendAcquireSatisfied,
        AdbServerActivationResult,
    ]
)
AdbServerRetireResult: TypeAlias = AdbServerAlreadyInactive | AdbServerDeactivationResult


class AdbServerLifecycleCoordinator:
    """Coordinate authoritative server state transitions around backend resources."""

    def __init__(
        self,
        state: AdbServerStateStore,
        *,
        backend: AdbServerBackend,
        endpoint_constraint: AdbServerEndpoint | None,
    ) -> None:
        if not isinstance(state, AdbServerStateStore):
            raise TypeError("state must be AdbServerStateStore")
        if not isinstance(backend, AdbServerBackend):
            raise TypeError("backend must satisfy AdbServerBackend")
        if endpoint_constraint is not None and not isinstance(endpoint_constraint, TcpAddress):
            raise TypeError("endpoint_constraint must be TcpAddress or None")
        self._state = state
        self._backend = backend
        self._endpoint_constraint = endpoint_constraint
        self._lock = RLock()

    def provision(self) -> AdbServerProvisionResult:
        """Return ordered raw evidence produced by one provision operation.

        Backend acquisition and state activation results are returned unchanged and in execution
        order. Already-active state is represented by :class:`AdbServerAlreadyActive` because no
        backend acquisition or state activation is performed in that path. A usable backend
        acquisition that loses the authoritative activation fence is released before its raw
        acquisition and activation evidence are returned.
        """

        with self._lock:
            t0 = self._state.snapshot()
            if t0.active:
                server = t0.server
                endpoint = t0.endpoint
                assert server is not None
                assert endpoint is not None
                return (AdbServerAlreadyActive(server, endpoint),)

            acquire = self._backend.acquire(self._endpoint_constraint)
            if isinstance(
                acquire,
                (
                    AdbServerBackendAcquireInProgress,
                    AdbServerBackendAcquireBlocked,
                    AdbServerBackendAcquireFailed,
                ),
            ):
                return (acquire,)
            if not isinstance(
                acquire,
                (AdbServerBackendAcquireSucceeded, AdbServerBackendAcquireSatisfied),
            ):
                raise TypeError("server backend acquire() returned an unsupported result")

            endpoint = acquire.endpoint
            if self._endpoint_constraint is not None and endpoint != self._endpoint_constraint:
                self._backend.release(endpoint)
                raise AdbServerLifecycleConsistencyError(
                    "endpoint-constrained ADB server backend acquisition returned a different endpoint"
                )

            try:
                activation = self._state.activate(endpoint, t0)
            except BaseException:
                self._backend.release(endpoint)
                raise

            if isinstance(activation, AdbServerActivationStateConflict):
                self._backend.release(endpoint)
            elif not isinstance(activation, AdbServerActivated):
                self._backend.release(endpoint)
                raise TypeError("server state activate() returned an unsupported result")

            return (acquire, activation)

    def retire(
        self,
        *,
        expected_server: AdbServerIdentity | None = None,
    ) -> AdbServerRetireResult:
        """Return typed evidence produced by one authoritative retirement operation.

        An unfenced call against an inactive state returns :class:`AdbServerAlreadyInactive`.
        Fenced calls pass the requested server identity directly to authoritative state so stale
        work is preserved as :class:`AdbServerDeactivationStateConflict` evidence. Backend release
        runs only after a committed deactivation.
        """

        if expected_server is not None and not isinstance(expected_server, AdbServerIdentity):
            raise TypeError("expected_server must be AdbServerIdentity or None")

        with self._lock:
            t0 = self._state.snapshot()
            if expected_server is None:
                server = t0.server
                if server is None:
                    return AdbServerAlreadyInactive(t0)
            else:
                server = expected_server

            deactivation = self._state.deactivate(server)
            if isinstance(deactivation, AdbServerDeactivationStateConflict):
                return deactivation
            if not isinstance(deactivation, AdbServerDeactivated):
                raise TypeError("server state deactivate() returned an unsupported result")

            committed_endpoint = deactivation.state.endpoint
            assert committed_endpoint is not None
            self._backend.release(committed_endpoint)
            return deactivation

    def configure_endpoint_constraint(self, endpoint_constraint: AdbServerEndpoint | None) -> None:
        """Replace the endpoint constraint used by subsequent acquisition attempts."""

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
