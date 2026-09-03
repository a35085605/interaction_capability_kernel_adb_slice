from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import TypeAlias

from networking import TcpAddress
from adb.server.candidate import AdbServerCandidate
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity, AdbServerIdentityIssuer
from adb.server.lifecycle.backend import (
    AdbServerBackend,
    AdbServerBackendAcquireBlocked,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireInProgress,
    AdbServerBackendAcquireResult,
    AdbServerBackendAcquireAlreadySatisfied,
    AdbServerBackendAcquireAchieved,
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
        AdbServerBackendAcquireAchieved | AdbServerBackendAcquireAlreadySatisfied,
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
        identity_issuer: AdbServerIdentityIssuer,
    ) -> None:
        if not isinstance(state, AdbServerStateStore):
            raise TypeError("state must be AdbServerStateStore")
        if not isinstance(backend, AdbServerBackend):
            raise TypeError("backend must satisfy AdbServerBackend")
        if endpoint_constraint is not None and not isinstance(endpoint_constraint, TcpAddress):
            raise TypeError("endpoint_constraint must be TcpAddress or None")
        if not isinstance(identity_issuer, AdbServerIdentityIssuer):
            raise TypeError("identity_issuer must be AdbServerIdentityIssuer")
        self._state = state
        self._backend = backend
        self._endpoint_constraint = endpoint_constraint
        self._identity_issuer = identity_issuer
        self._lock = RLock()

    def provision(self) -> AdbServerProvisionResult:
        """Return ordered raw evidence produced by one provision operation.

        Provisioning executes in two phases: backend acquisition first satisfies the requested
        attachment acquisition, then authoritative state activation commits that attachment as the
        runtime server. Backend acquisition and state activation results are returned unchanged
        and in execution order. Already-active state is represented by
        :class:`AdbServerAlreadyActive` because neither phase is performed in that path. If this
        provision call newly achieves an acquisition that cannot be committed, that acquisition is
        relinquished before its raw acquisition and activation evidence are returned.
        """

        with self._lock:
            t0 = self._state.snapshot()
            if t0.active:
                server = t0.server
                endpoint = t0.endpoint
                assert server is not None
                assert endpoint is not None
                return (AdbServerAlreadyActive(server, endpoint),)

            acquisition = self._acquire_backend()
            if isinstance(
                acquisition,
                (
                    AdbServerBackendAcquireInProgress,
                    AdbServerBackendAcquireBlocked,
                    AdbServerBackendAcquireFailed,
                ),
            ):
                return (acquisition,)

            activation = self._commit_acquisition(
                acquisition,
                expected_state=t0,
            )
            return (acquisition, activation)

    def _acquire_backend(self) -> AdbServerBackendAcquireResult:
        """Run the backend-acquisition phase and validate its usable attachment."""

        acquisition = self._backend.acquire(self._endpoint_constraint)
        if isinstance(
            acquisition,
            (
                AdbServerBackendAcquireInProgress,
                AdbServerBackendAcquireBlocked,
                AdbServerBackendAcquireFailed,
            ),
        ):
            return acquisition
        if not isinstance(
            acquisition,
            (AdbServerBackendAcquireAchieved, AdbServerBackendAcquireAlreadySatisfied),
        ):
            raise TypeError("server backend acquire() returned an unsupported result")

        endpoint = acquisition.endpoint
        if self._endpoint_constraint is not None and endpoint != self._endpoint_constraint:
            self._rollback_acquisition(acquisition)
            raise AdbServerLifecycleConsistencyError(
                "endpoint-constrained ADB server backend acquisition returned a different endpoint"
            )
        return acquisition

    def _commit_acquisition(
        self,
        acquisition: AdbServerBackendAcquireAchieved | AdbServerBackendAcquireAlreadySatisfied,
        *,
        expected_state: AdbServerState,
    ) -> AdbServerActivationResult:
        """Commit one usable backend acquisition and rollback only effects of this provision."""

        endpoint = acquisition.endpoint
        try:
            candidate = AdbServerCandidate(
                identity=self._identity_issuer.issue(),
                endpoint=endpoint,
            )
            activation = self._state.activate(candidate, expected_state)
        except BaseException:
            self._rollback_acquisition(acquisition)
            raise

        if isinstance(activation, AdbServerActivationStateConflict):
            self._rollback_acquisition(acquisition)
        elif not isinstance(activation, AdbServerActivated):
            self._rollback_acquisition(acquisition)
            raise TypeError("server state activate() returned an unsupported result")
        return activation

    def _rollback_acquisition(
        self,
        acquisition: AdbServerBackendAcquireAchieved | AdbServerBackendAcquireAlreadySatisfied,
    ) -> None:
        """Relinquish only an acquisition established by the current provision call."""

        if isinstance(acquisition, AdbServerBackendAcquireAchieved):
            self._backend.release(acquisition.endpoint)

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

            deactivation = self._commit_retirement(server)
            if isinstance(deactivation, AdbServerDeactivationStateConflict):
                return deactivation

            self._release_deactivated_server(deactivation)
            return deactivation

    def _commit_retirement(
        self,
        server: AdbServerIdentity,
    ) -> AdbServerDeactivationResult:
        """Commit authoritative deactivation before relinquishing the backend attachment."""

        deactivation = self._state.deactivate(server)
        if isinstance(deactivation, AdbServerDeactivationStateConflict):
            return deactivation
        if not isinstance(deactivation, AdbServerDeactivated):
            raise TypeError("server state deactivate() returned an unsupported result")
        return deactivation

    def _release_deactivated_server(self, deactivation: AdbServerDeactivated) -> None:
        """Relinquish the backend attachment recorded by one committed deactivation."""

        committed_endpoint = deactivation.state.endpoint
        assert committed_endpoint is not None
        self._backend.release(committed_endpoint)

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
