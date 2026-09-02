from __future__ import annotations

from threading import RLock

from adb.runtime.state import AdbRuntimeState
from adb.server.identity import AdbServerIdentity
from adb.server.lifecycle.control.errors import AdbServerControlError
from adb.server.lifecycle.control.provisioner import AdbServerProvisioner
from adb.server.lifecycle.control.retirer import AdbServerRetirer
from adb.server.lifecycle.control.result import (
    AdbServerProvisionDeferred,
    AdbServerProvisioned,
)
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
    AdbServerProvisionCommitted,
    AdbServerProvisionTransactionResult,
)
from adb.server.state import AdbServerActivated, AdbServerDeactivated, AdbServerState


class AdbServerLifecycleRuntimeFacade:
    """Orchestrate authoritative ADB server lifecycle transactions for one runtime."""

    def __init__(
        self,
        state: AdbRuntimeState,
        *,
        provisioner: AdbServerProvisioner,
        retirer: AdbServerRetirer,
    ) -> None:
        if not isinstance(state, AdbRuntimeState):
            raise TypeError("state must be AdbRuntimeState")
        if not isinstance(provisioner, AdbServerProvisioner):
            raise TypeError("provisioner must be AdbServerProvisioner")
        if not isinstance(retirer, AdbServerRetirer):
            raise TypeError("retirer must be AdbServerRetirer")
        self._state = state
        self._provisioner = provisioner
        self._retirer = retirer
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
            result = self.provision()
            return transition_lifecycle_result(
                transition,
                result,
                self._state.observe_server(),
            )
        elif isinstance(transition, AdbServerRetireAction):
            result = self._retire(
                expected=None,
                expected_server=transition.expected_server,
            )
            return transition_lifecycle_result(transition, result)
        else:
            return transition

    def replace_provisioner(self, provisioner: AdbServerProvisioner) -> None:
        """Replace provisioning policy while preserving this runtime lifecycle authority."""

        if not isinstance(provisioner, AdbServerProvisioner):
            raise TypeError("provisioner must be AdbServerProvisioner")
        with self._lock:
            self._provisioner = provisioner

    def _provision(
        self,
        *,
        expected: AdbServerState | None,
    ) -> AdbServerProvisionTransactionResult | None:
        cleanup_endpoint = None
        with self._lock:
            t0 = self._state.observe_server()
            if expected is not None and t0 != expected:
                return None
            if t0.active:
                return AdbServerProvisionDeferred(
                    "ADB runtime already has an active server lifetime"
                )

            result = self._provisioner.provision()
            if not isinstance(result, AdbServerProvisioned):
                return result

            try:
                activation = self._state.activate_server(result.endpoint, t0)
            except BaseException:
                try:
                    self._retirer.retire(result.endpoint)
                except BaseException as release_error:
                    raise AdbServerControlError(
                        "ADB runtime server state commit failed and its provisioned backend "
                        "attachment could not be released"
                    ) from release_error
                raise

            if isinstance(activation, AdbServerActivated):
                return AdbServerProvisionCommitted(activation.server, activation)
            cleanup_endpoint = result.endpoint

        # A provisioned endpoint that lost its observed inactive-state comparison never became
        # authoritative. Cleanup is outside the facade lock so newer lifecycle work is not blocked.
        assert cleanup_endpoint is not None
        self._retirer.retire(cleanup_endpoint)
        return AdbServerProvisionDeferred(
            "ADB runtime server state changed before provisioned endpoint could commit"
        )

    def _retire(
        self,
        *,
        expected: AdbServerState | None,
        expected_server: AdbServerIdentity | None,
    ) -> AdbServerDeactivated | None:
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

        # Domain deactivation is authoritative before backend cleanup begins. The state CAS is the
        # retirement linearization point, so successor provisioning may race backend cleanup.
        committed_endpoint = deactivation.state.endpoint
        assert committed_endpoint is not None
        self._retirer.retire(committed_endpoint)
        return deactivation


__all__ = ["AdbServerLifecycleRuntimeFacade"]
