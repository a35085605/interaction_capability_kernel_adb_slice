from __future__ import annotations

from threading import RLock

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
    AdbServerActivationStateConflict,
    AdbServerDeactivated,
    AdbServerStateStore,
)


class AdbServerLifecycleCoordinator:
    """Coordinate authoritative server state transitions around backend resources."""

    def __init__(
        self,
        state: AdbServerStateStore,
        *,
        backend: AdbServerBackend,
        provision_endpoint: AdbServerEndpoint | None,
    ) -> None:
        if not isinstance(state, AdbServerStateStore):
            raise TypeError("state must be AdbServerStateStore")
        if not isinstance(backend, AdbServerBackend):
            raise TypeError("backend must satisfy AdbServerBackend")
        if provision_endpoint is not None and not isinstance(provision_endpoint, TcpAddress):
            raise TypeError("provision_endpoint must be TcpAddress or None")
        self._state = state
        self._backend = backend
        self._provision_endpoint = provision_endpoint
        self._lock = RLock()

    def acquire_once(self) -> AdbServerBackendAcquireResult | None:
        """Execute at most one backend acquisition and commit a usable endpoint when still valid.

        ``None`` means the authoritative server state was already active when this operation
        linearized, so no backend acquisition was attempted. Otherwise the raw backend acquisition
        evidence is returned unchanged. State activation and backend release are deliberately
        execution concerns and are not wrapped in a second result algebra.
        """

        with self._lock:
            t0 = self._state.snapshot()
            if t0.active:
                return None

            acquire = self._backend.acquire(self._provision_endpoint)
            if isinstance(
                acquire,
                (
                    AdbServerBackendAcquireInProgress,
                    AdbServerBackendAcquireBlocked,
                    AdbServerBackendAcquireFailed,
                ),
            ):
                return acquire
            if not isinstance(
                acquire,
                (AdbServerBackendAcquireSucceeded, AdbServerBackendAcquireSatisfied),
            ):
                raise TypeError("server backend acquire() returned an unsupported result")

            endpoint = acquire.endpoint
            if self._provision_endpoint is not None and endpoint != self._provision_endpoint:
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

            return acquire

    def retire(
        self,
        *,
        expected_server: AdbServerIdentity | None = None,
    ) -> AdbServerDeactivated | None:
        """Retire the authoritative server, optionally fenced by its identity."""

        if expected_server is not None and not isinstance(expected_server, AdbServerIdentity):
            raise TypeError("expected_server must be AdbServerIdentity or None")

        with self._lock:
            t0 = self._state.snapshot()
            server = t0.server
            endpoint = t0.endpoint
            if server is None or endpoint is None:
                return None
            if expected_server is not None and server != expected_server:
                return None

            deactivation = self._state.deactivate(server)
            if not isinstance(deactivation, AdbServerDeactivated):
                return None

            committed_endpoint = deactivation.state.endpoint
            assert committed_endpoint is not None
            self._backend.release(committed_endpoint)
            return deactivation

    def configure_provision_endpoint(self, endpoint: AdbServerEndpoint | None) -> None:
        """Replace the endpoint constraint used by subsequent acquisition attempts."""

        if endpoint is not None and not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress or None")
        with self._lock:
            self._provision_endpoint = endpoint


__all__ = ["AdbServerLifecycleCoordinator"]
