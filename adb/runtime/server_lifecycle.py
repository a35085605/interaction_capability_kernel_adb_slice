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
    AdbServerBackendAcquireSatisfied,
    AdbServerBackendAcquireSucceeded,
)
from adb.server.lifecycle.control.errors import AdbServerLifecycleConsistencyError
from adb.server.lifecycle.supervision.intent import (
    AdbServerLifecycleIntent,
    AdbServerLifecycleIntentResult,
)
from adb.server.lifecycle.supervision.transition import (
    AdbServerProvisionAction,
    AdbServerRetireAction,
    transition_lifecycle_intent,
    transition_lifecycle_result,
)
from adb.server.lifecycle.transaction import (
    AdbServerProvisionAcquireStopped,
    AdbServerProvisionActivationAttempted,
    AdbServerProvisionStateConflict,
    AdbServerProvisionTransactionResult,
)
from adb.server.state import (
    AdbServerActivated,
    AdbServerActivationRejected,
    AdbServerDeactivated,
    AdbServerState,
)


class AdbServerLifecycleRuntimeFacade:
    """Orchestrate authoritative ADB server lifecycle transactions for one runtime."""

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

    def provision(self) -> AdbServerProvisionTransactionResult:
        """Provision against the authoritative server state observed at execution time."""

        result = self._provision(expected=None)
        assert result is not None
        return result

    def provision_if_current(
        self,
        expected: AdbServerState,
    ) -> AdbServerProvisionTransactionResult | None:
        """Conditionally provision from ``expected``, returning ``None`` for stale state."""

        if not isinstance(expected, AdbServerState):
            raise TypeError("expected must be AdbServerState")
        return self._provision(expected=expected)

    def retire(self) -> bool:
        """Retire the authoritative server lifetime observed at execution time."""

        return self._retire(expected=None, expected_server=None) is not None

    def retire_if_current(self, expected: AdbServerState) -> bool:
        """Conditionally retire ``expected`` when it still matches authoritative state."""

        if not isinstance(expected, AdbServerState):
            raise TypeError("expected must be AdbServerState")
        return self._retire(expected=expected, expected_server=None) is not None

    def dispatch(
        self,
        intent: AdbServerLifecycleIntent,
    ) -> AdbServerLifecycleIntentResult:
        """Execute one interpreted lifecycle action through authoritative runtime transactions."""

        transition = transition_lifecycle_intent(intent, self._state.observe_server())
        if isinstance(transition, AdbServerProvisionAction):
            return transition_lifecycle_result(transition, self.provision())
        elif isinstance(transition, AdbServerRetireAction):
            result = self._retire(
                expected=None,
                expected_server=transition.expected_server,
            )
            return transition_lifecycle_result(transition, result)
        else:
            return transition

    def configure_provision_endpoint(self, endpoint: AdbServerEndpoint | None) -> None:
        """Replace the endpoint constraint used by subsequent provisioning attempts."""

        if endpoint is not None and not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress or None")
        with self._lock:
            self._provision_endpoint = endpoint

    def _provision(
        self,
        *,
        expected: AdbServerState | None,
    ) -> AdbServerProvisionTransactionResult | None:
        with self._lock:
            t0 = self._state.observe_server()
            if expected is not None and t0 != expected:
                return None
            if t0.active:
                return AdbServerProvisionStateConflict(t0)

            acquire = self._backend.acquire(self._provision_endpoint)
            if isinstance(
                acquire,
                (
                    AdbServerBackendAcquireInProgress,
                    AdbServerBackendAcquireBlocked,
                    AdbServerBackendAcquireFailed,
                ),
            ):
                return AdbServerProvisionAcquireStopped(acquire)
            if not isinstance(
                acquire,
                (AdbServerBackendAcquireSucceeded, AdbServerBackendAcquireSatisfied),
            ):
                raise TypeError("server backend acquire() returned an unsupported result")

            endpoint = acquire.endpoint
            if self._provision_endpoint is not None and endpoint != self._provision_endpoint:
                self._backend.release(endpoint)
                raise AdbServerLifecycleConsistencyError(
                    "endpoint-constrained ADB server backend acquisition returned a "
                    "different endpoint"
                )

            try:
                activation = self._state.activate_server(endpoint, t0)
            except BaseException:
                # The endpoint never became authoritative. Backend release only transfers cleanup
                # responsibility; physical attachment convergence remains an implementation detail.
                self._backend.release(endpoint)
                raise

            if isinstance(activation, AdbServerActivationRejected):
                # The acquired endpoint lost its inactive-state comparison and never became
                # authoritative. Relinquish it before allowing a successor lifecycle transaction.
                self._backend.release(endpoint)
            elif not isinstance(activation, AdbServerActivated):
                self._backend.release(endpoint)
                raise TypeError("runtime state activate_server() returned an unsupported result")

            return AdbServerProvisionActivationAttempted(acquire, activation)

    def _retire(
        self,
        *,
        expected: AdbServerState | None,
        expected_server: AdbServerIdentity | None,
    ) -> AdbServerDeactivated | None:
        with self._lock:
            t0 = self._state.observe_server()
            if expected is not None and t0 != expected:
                return None
            server = t0.server
            endpoint = t0.endpoint
            if server is None or endpoint is None:
                return None
            if expected_server is not None and server != expected_server:
                return None
            deactivation = self._state.deactivate_server(server)
            if not isinstance(deactivation, AdbServerDeactivated):
                return None

            # Domain deactivation is the retirement linearization point. Runtime only relinquishes
            # the old endpoint; whether or when its physical attachment ends belongs to Backend.
            committed_endpoint = deactivation.state.endpoint
            assert committed_endpoint is not None
            self._backend.release(committed_endpoint)
            return deactivation


__all__ = ["AdbServerLifecycleRuntimeFacade"]
