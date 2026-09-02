from __future__ import annotations

from threading import RLock

from networking import TcpAddress
from adb.runtime.state import AdbRuntimeState
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity
from adb.server.lifecycle.control.backend import (
    AdbServerBackend,
    AdbServerBackendAcquireBlocked,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireInProgress,
    AdbServerBackendAcquireResult,
    AdbServerBackendAcquireSatisfied,
    AdbServerBackendAcquireSucceeded,
)
from adb.server.lifecycle.control.errors import AdbServerLifecycleConsistencyError
from adb.server.state import (
    AdbServerActivated,
    AdbServerActivationRejected,
    AdbServerDeactivated,
)


class AdbServerLifecycleRuntimeFacade:
    """Own authoritative runtime server activation/retirement around backend resources."""

    def __init__(
        self,
        state: AdbRuntimeState,
        *,
        backend: AdbServerBackend,
        provision_endpoint: AdbServerEndpoint | None,
    ) -> None:
        if not isinstance(state, AdbRuntimeState):
            raise TypeError("state must be AdbRuntimeState")
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

        ``None`` means the authoritative runtime already had an active server when this operation
        linearized, so no backend acquisition was attempted. Otherwise the raw backend acquisition
        evidence is returned unchanged. Runtime activation/release is deliberately an execution
        concern and is not wrapped in a second result algebra.
        """

        with self._lock:
            t0 = self._state.observe_server()
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
                activation = self._state.activate_server(endpoint, t0)
            except BaseException:
                self._backend.release(endpoint)
                raise

            if isinstance(activation, AdbServerActivationRejected):
                self._backend.release(endpoint)
            elif not isinstance(activation, AdbServerActivated):
                self._backend.release(endpoint)
                raise TypeError("runtime state activate_server() returned an unsupported result")

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
            t0 = self._state.observe_server()
            server = t0.server
            endpoint = t0.endpoint
            if server is None or endpoint is None:
                return None
            if expected_server is not None and server != expected_server:
                return None

            deactivation = self._state.deactivate_server(server)
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


__all__ = ["AdbServerLifecycleRuntimeFacade"]
