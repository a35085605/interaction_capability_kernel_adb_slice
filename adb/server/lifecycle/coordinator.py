from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Protocol, TypeAlias, runtime_checkable

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
    AdbServerStateView,
    AdbServerStateWriter,
)


_RLockType = type(RLock())


@runtime_checkable
class _AdbServerStateAccess(
    AdbServerStateView,
    AdbServerStateWriter,
    Protocol,
):
    """Read and commit authoritative server state."""


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
        AdbServerBackendAlreadyAcquired
        | AdbServerBackendAcquireDeferred
        | AdbServerBackendAcquireFailed
    ]
    | tuple[
        AdbServerBackendAcquired,
        AdbServerActivationResult,
    ]
)
AdbServerRetireResult: TypeAlias = AdbServerAlreadyInactive | AdbServerDeactivationResult


class AdbServerLifecycleCoordinator:
    """Coordinate backend effects with fenced authoritative server-state transitions."""

    def __init__(
        self,
        state: _AdbServerStateAccess,
        *,
        backend: AdbServerBackend,
        endpoint_constraint: AdbServerEndpoint | None,
        publisher: EventPublisher | None = None,
        authority_lock: _RLockType | None = None,
    ) -> None:
        if not isinstance(state, _AdbServerStateAccess):
            raise TypeError(
                "state must satisfy AdbServerStateView and AdbServerStateWriter"
            )
        if not isinstance(backend, AdbServerBackend):
            raise TypeError("backend must satisfy AdbServerBackend")
        if endpoint_constraint is not None and not isinstance(endpoint_constraint, TcpAddress):
            raise TypeError("endpoint_constraint must be TcpAddress or None")
        if publisher is not None and not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher or be None")
        if authority_lock is not None and not isinstance(authority_lock, _RLockType):
            raise TypeError("authority_lock must be a reentrant lock or None")
        self._state = state
        self._backend = backend
        self._endpoint_constraint = endpoint_constraint
        self._publisher = publisher
        # Share only the short cross-aggregate authority check/transition boundary. Backend effects
        # are independently linearizable and intentionally execute outside this lock.
        self._authority_lock = RLock() if authority_lock is None else authority_lock

    def provision(self) -> AdbServerProvisionResult:
        """Acquire server access and commit it as the authoritative server.

        Returns ordered acquisition and activation evidence. A newly acquired backend
        handle is released when its activation fence is lost.
        """

        with self._authority_lock:
            endpoint_constraint = self._endpoint_constraint
            t0 = self._state.snapshot()
            if t0.active:
                server = t0.current_identity
                endpoint = t0.endpoint
                assert server is not None
                assert endpoint is not None
                return (AdbServerAlreadyActive(server, endpoint),)

        acquisition = self._acquire_backend(endpoint_constraint)
        if not isinstance(acquisition, AdbServerBackendAcquired):
            return (acquisition,)

        try:
            with self._authority_lock:
                activation = self._commit_activation(
                    acquisition.endpoint,
                    expected=t0.identity,
                )
        except BaseException:
            self._rollback_acquisition(acquisition)
            raise

        if isinstance(activation, AdbServerActivationStateConflict):
            self._rollback_acquisition(acquisition)
        elif not isinstance(activation, AdbServerActivated):
            self._rollback_acquisition(acquisition)
            raise TypeError("server state activate() returned an unsupported result")
        result = (acquisition, activation)

        if isinstance(activation, AdbServerActivated) and self._publisher is not None:
            self._publisher.publish(activation)
        return result

    def _acquire_backend(
        self,
        endpoint_constraint: AdbServerEndpoint | None,
    ) -> AdbServerBackendAcquireResult:
        """Run one backend acquisition against the operation's captured endpoint constraint."""

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
            self._rollback_acquisition(acquisition)
            raise AdbServerLifecycleConsistencyError(
                "endpoint-constrained ADB server backend acquisition returned a different endpoint"
            )
        return acquisition

    def _commit_activation(
        self,
        endpoint: AdbServerEndpoint,
        *,
        expected: AdbServerIdentity | None,
    ) -> AdbServerActivationResult:
        """Commit one newly acquired endpoint at the shared authority boundary."""

        return self._state.activate(endpoint, expected=expected)

    def _rollback_acquisition(self, acquisition: AdbServerBackendAcquired) -> None:
        """Relinquish an acquisition established by the current provision invocation."""

        if not isinstance(acquisition, AdbServerBackendAcquired):
            raise TypeError("acquisition must be AdbServerBackendAcquired")
        self._backend.release()

    def retire(
        self,
        *,
        expected_server: AdbServerIdentity | None = None,
    ) -> AdbServerRetireResult:
        """Retire the authoritative server and release its backend access.

        ``expected_server`` fences stale retirement requests. A committed deactivation is
        published after backend release is requested.
        """

        if expected_server is not None and not isinstance(expected_server, AdbServerIdentity):
            raise TypeError("expected_server must be AdbServerIdentity or None")

        with self._authority_lock:
            t0 = self._state.snapshot()
            if expected_server is None:
                server = t0.current_identity
                if server is None:
                    return AdbServerAlreadyInactive(t0)
            else:
                server = expected_server

            deactivation = self._commit_retirement(server)
        if isinstance(deactivation, AdbServerDeactivationStateConflict):
            return deactivation

        self._release_deactivated_server()

        if self._publisher is not None:
            self._publisher.publish(deactivation)
        return deactivation

    def _commit_retirement(
        self,
        server: AdbServerIdentity,
    ) -> AdbServerDeactivationResult:
        """Commit authoritative deactivation before relinquishing the backend acquisition."""

        deactivation = self._state.deactivate(server)
        if isinstance(deactivation, AdbServerDeactivationStateConflict):
            return deactivation
        if not isinstance(deactivation, AdbServerDeactivated):
            raise TypeError("server state deactivate() returned an unsupported result")
        return deactivation

    def _release_deactivated_server(self) -> None:
        """Relinquish the backend acquisition after one committed deactivation."""

        self._backend.release()

    def configure_endpoint_constraint(self, endpoint_constraint: AdbServerEndpoint | None) -> None:
        """Replace the endpoint constraint captured by subsequent acquisition attempts."""

        if endpoint_constraint is not None and not isinstance(endpoint_constraint, TcpAddress):
            raise TypeError("endpoint_constraint must be TcpAddress or None")
        with self._authority_lock:
            self._endpoint_constraint = endpoint_constraint


__all__ = [
    "AdbServerAlreadyActive",
    "AdbServerAlreadyInactive",
    "AdbServerLifecycleCoordinator",
    "AdbServerProvisionResult",
    "AdbServerRetireResult",
]
