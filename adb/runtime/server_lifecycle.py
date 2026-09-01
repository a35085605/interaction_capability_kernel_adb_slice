from __future__ import annotations

from threading import RLock

from adb.runtime.state import AdbRuntimeState
from adb.server.lifetime import AdbServerLifetime
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
    AdbServerProvisionIntent,
    AdbServerRetireIntent,
)
from adb.server.lifecycle.transaction import (
    AdbServerProvisionCommitted,
    AdbServerProvisionTransactionResult,
)
from adb.server.state import AdbServerStateSnapshot


class AdbServerLifecycleRuntimeFacade:
    """Coordinate authoritative ADB server activation and deactivation for one runtime."""

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
        expected: AdbServerStateSnapshot,
    ) -> AdbServerProvisionTransactionResult | None:
        """Conditionally provision from ``expected``, returning ``None`` for stale state."""

        if not isinstance(expected, AdbServerStateSnapshot):
            raise TypeError("expected must be AdbServerStateSnapshot")
        return self._provision(expected=expected)

    def retire(self) -> bool:
        """Retire the authoritative server lifetime observed at execution time."""

        return self._retire(expected=None, expected_server=None)

    def retire_if_current(self, expected: AdbServerStateSnapshot) -> bool:
        """Conditionally retire ``expected`` when it still matches authoritative state."""

        if not isinstance(expected, AdbServerStateSnapshot):
            raise TypeError("expected must be AdbServerStateSnapshot")
        return self._retire(expected=expected, expected_server=None)

    def dispatch(
        self,
        intent: AdbServerLifecycleIntent,
    ) -> AdbServerLifecycleIntentResult:
        """Dispatch one complete runtime-owned server lifecycle intent transaction."""

        if isinstance(intent, AdbServerProvisionIntent):
            return self.provision()
        if isinstance(intent, AdbServerRetireIntent):
            return self._retire(expected=None, expected_server=intent.server)
        raise TypeError("unsupported ADB server lifecycle intent")

    def replace_provisioner(self, provisioner: AdbServerProvisioner) -> None:
        """Replace provisioning policy while preserving this runtime lifecycle authority."""

        if not isinstance(provisioner, AdbServerProvisioner):
            raise TypeError("provisioner must be AdbServerProvisioner")
        with self._lock:
            self._provisioner = provisioner

    def _provision(
        self,
        *,
        expected: AdbServerStateSnapshot | None,
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
                committed = self._state.commit_server(result.endpoint, t0.epoch)
            except BaseException:
                try:
                    self._retirer.retire(result.endpoint)
                except BaseException as release_error:
                    raise AdbServerControlError(
                        "ADB runtime server state commit failed and its provisioned backend "
                        "attachment could not be released"
                    ) from release_error
                raise

            if committed is not None:
                return AdbServerProvisionCommitted(committed)
            cleanup_endpoint = result.endpoint

        # A provisioned endpoint that lost its inactive-epoch comparison never became
        # authoritative. Cleanup is outside the facade lock so newer lifecycle work is not blocked.
        assert cleanup_endpoint is not None
        self._retirer.retire(cleanup_endpoint)
        return AdbServerProvisionDeferred(
            "ADB runtime server state changed before provisioned endpoint could commit"
        )

    def _retire(
        self,
        *,
        expected: AdbServerStateSnapshot | None,
        expected_server: AdbServerLifetime | None,
    ) -> bool:
        with self._lock:
            t0 = self._state.observe_server()
            if expected is not None and t0 != expected:
                return False
            server = t0.current
            if server is None:
                return False
            if expected_server is not None and server != expected_server:
                return False
            if not self._state.deactivate_server(server):
                return False

        # Domain deactivation is authoritative before backend cleanup begins. Releasing outside the
        # facade lock permits successor provisioning to race cleanup, matching runtime semantics.
        self._retirer.retire(server.endpoint)
        return True


__all__ = ["AdbServerLifecycleRuntimeFacade"]
