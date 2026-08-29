from __future__ import annotations

from threading import RLock

from adb.runtime.state import AdbRuntimeState
from adb.server.lifecycle.control.provisioner import AdbServerProvisioner
from adb.server.lifecycle.control.retirer import AdbServerRetirer
from adb.server.lifecycle.control.result import (
    AdbServerProvisionDeferred,
    AdbServerProvisionResult,
    AdbServerProvisioned,
)
from adb.server.lifecycle.supervision.intent import (
    AdbServerLifecycleIntent,
    AdbServerLifecycleIntentResult,
    AdbServerProvisionIntent,
    AdbServerRetireIntent,
)
from adb.server.state import AdbServerStateTransition


class AdbServerLifecycleRuntimeFacade:
    """Runtime-owned server lifecycle transaction facade.

    Provisioning performs its external side effect first and commits the resulting candidate only
    if the T0 server-state snapshot still matches.  Retirement performs the inverse ordering: it
    first removes the exact current lifetime from authoritative state, then releases its backend
    attachment.  Supervisors therefore express lifecycle intent without coordinating control and
    state mutations themselves.
    """

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

    def dispatch(
        self,
        intent: AdbServerLifecycleIntent,
    ) -> AdbServerLifecycleIntentResult:
        """Interpret one lifecycle intent as a complete runtime-owned transaction."""

        if isinstance(intent, AdbServerProvisionIntent):
            return self._provision()
        if isinstance(intent, AdbServerRetireIntent):
            return self._retire(intent)
        raise TypeError("unsupported ADB server lifecycle intent")

    def replace_provisioner(self, provisioner: AdbServerProvisioner) -> None:
        """Replace provisioning policy while preserving this runtime lifecycle authority."""

        if not isinstance(provisioner, AdbServerProvisioner):
            raise TypeError("provisioner must be AdbServerProvisioner")
        with self._lock:
            self._provisioner = provisioner

    def _provision(self) -> AdbServerProvisionResult:
        stale_server = None
        with self._lock:
            t0 = self._state.observe_server()
            if t0.current is not None:
                return AdbServerProvisionDeferred(
                    "ADB runtime already has an active server lifetime"
                )

            result = self._provisioner.provision()
            if not isinstance(result, AdbServerProvisioned):
                return result

            candidate = result.server
            if self._state.commit_server(AdbServerStateTransition(t0, candidate)):
                return result
            stale_server = candidate

        # A candidate that lost its T0 comparison never became authoritative.  Cleanup is outside
        # the facade lock so a newer runtime transition is not blocked by backend release work.
        self._retirer.retire(stale_server)
        return AdbServerProvisionDeferred(
            "ADB runtime server state changed before provisioned lifetime could commit"
        )

    def _retire(self, intent: AdbServerRetireIntent) -> bool:
        server = intent.server
        with self._lock:
            t0 = self._state.observe_server()
            if t0.current != server:
                return False
            if not self._state.commit_server(AdbServerStateTransition(t0, None)):
                return False

        # Domain retirement is authoritative before backend cleanup begins.  Releasing outside the
        # facade lock permits successor provisioning to race cleanup, matching runtime semantics.
        self._retirer.retire(server)
        return True


__all__ = ["AdbServerLifecycleRuntimeFacade"]
