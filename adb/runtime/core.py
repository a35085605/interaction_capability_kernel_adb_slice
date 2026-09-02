from __future__ import annotations

from threading import RLock

from adb.runtime.managed import AdbManagedRuntime
from adb.server.lifecycle.control.errors import AdbServerControlError
from adb.server.lifecycle.control.provisioner import AdbServerProvisioner
from adb.server.lifecycle.control.retirer import AdbServerRetirer
from adb.server.lifecycle.control.result import (
    AdbServerProvisionDeferred,
    AdbServerProvisionFailed,
)
from adb.server.lifecycle.supervision.intent import (
    AdbServerLifecycleIntent,
    AdbServerLifecycleIntentResult,
    AdbServerReconcileIntent,
)
from adb.server.lifecycle.transaction import (
    AdbServerProvisionCommitted,
    AdbServerProvisionTransactionResult,
)
from adb.server.lifecycle.supervision.policy import AdbServerRecoveryPolicy
from adb.server.lifecycle.supervision.recovery import AdbServerRecoveryCycle
from adb.server.signal import (
    AdbServerReconciliationRequested,
    AdbServerRecovered,
    AdbServerRetired,
)
from adb.runtime.server_lifecycle import AdbServerLifecycleRuntimeFacade
from adb.runtime.state import AdbRuntimeState
from adb.server.state import AdbServerState
from adb.transport.configuration import AdbConfiguredTransport
from adb.tracking.snapshot.state import AdbTransportListSnapshotView
from adb.tracking.supervision.supervisor import AdbTransportListWatchSupervisor
from adb.transport.lifecycle.supervision.policy import AdbConfiguredTransportSupervisionPolicy
from adb.transport.lifecycle.supervision.supervisor import AdbConfiguredTransportSupervisor
from eventing import EventBus, EventSubscriptionToken
from scheduling import TemporalScheduler


class AdbRuntime(AdbManagedRuntime):
    """Own the composed ADB capability graph and its authoritative server and transport-list
    state.
    """

    def __init__(
        self,
        state: AdbRuntimeState,
        *,
        server_provisioner: AdbServerProvisioner,
        server_retirer: AdbServerRetirer,
        event_bus: EventBus | None = None,
        server_supervision_scheduler: TemporalScheduler[object] | None = None,
        server_supervision_policy: AdbServerRecoveryPolicy | None = None,
        server_recovery_enabled: bool = True,
        transport_list_watch_supervisor: AdbTransportListWatchSupervisor | None = None,
        transport_supervisor: AdbConfiguredTransportSupervisor | None = None,
        transport_supervision_policy: AdbConfiguredTransportSupervisionPolicy | None = None,
        _bootstrap_server: bool = False,
    ) -> None:
        if not isinstance(state, AdbRuntimeState):
            raise TypeError("state must be AdbRuntimeState")
        if not isinstance(server_provisioner, AdbServerProvisioner):
            raise TypeError("server_provisioner must be AdbServerProvisioner")
        if not isinstance(server_retirer, AdbServerRetirer):
            raise TypeError("server_retirer must be AdbServerRetirer")
        if event_bus is not None and not _is_event_bus(event_bus):
            raise TypeError("event_bus must satisfy EventBus or be None")
        if server_supervision_scheduler is not None and not isinstance(
            server_supervision_scheduler, TemporalScheduler
        ):
            raise TypeError(
                "server_supervision_scheduler must satisfy TemporalScheduler or be None"
            )
        if server_supervision_policy is None:
            server_supervision_policy = AdbServerRecoveryPolicy()
        if not isinstance(server_supervision_policy, AdbServerRecoveryPolicy):
            raise TypeError(
                "server_supervision_policy must be AdbServerRecoveryPolicy or None"
            )
        if not isinstance(server_recovery_enabled, bool):
            raise TypeError("server_recovery_enabled must be bool")
        if not isinstance(_bootstrap_server, bool):
            raise TypeError("_bootstrap_server must be bool")
        if transport_list_watch_supervisor is not None and not isinstance(
            transport_list_watch_supervisor, AdbTransportListWatchSupervisor
        ):
            raise TypeError(
                "transport_list_watch_supervisor must be "
                "AdbTransportListWatchSupervisor or None"
            )
        if transport_supervisor is not None and not isinstance(
            transport_supervisor, AdbConfiguredTransportSupervisor
        ):
            raise TypeError(
                "transport_supervisor must be AdbConfiguredTransportSupervisor or None"
            )
        if transport_supervision_policy is None:
            transport_supervision_policy = AdbConfiguredTransportSupervisionPolicy()
        if not isinstance(
            transport_supervision_policy, AdbConfiguredTransportSupervisionPolicy
        ):
            raise TypeError(
                "transport_supervision_policy must be "
                "AdbConfiguredTransportSupervisionPolicy or None"
            )
        if any(
            component is not None
            for component in (
                server_supervision_scheduler,
                transport_list_watch_supervisor,
                transport_supervisor,
            )
        ) and event_bus is None:
            raise ValueError("supervised runtime components require an event bus")
        if (
            transport_list_watch_supervisor is not None
            and transport_list_watch_supervisor.server_state is not state.server
        ):
            raise ValueError("transport-list watch supervisor must share the runtime server state")
        if (
            transport_list_watch_supervisor is not None
            and transport_list_watch_supervisor.transport_list_state is not state.transport_list
        ):
            raise ValueError(
                "transport-list watch supervisor must share the runtime transport-list state"
            )
        if (
            transport_supervisor is not None
            and transport_supervisor.server_state is not state.server
        ):
            raise ValueError("transport supervisor must share the runtime server state")
        if (
            transport_supervisor is not None
            and transport_supervisor.transport_list_state is not state.transport_list
        ):
            raise ValueError("transport supervisor must share the runtime transport-list state")

        super().__init__(state.server)
        self._state = state
        self._event_bus = event_bus
        self._server_lifecycle = AdbServerLifecycleRuntimeFacade(
            state,
            provisioner=server_provisioner,
            retirer=server_retirer,
        )
        if _bootstrap_server:
            self._bootstrap_initial_server()

        self._server_recovery_scheduler = server_supervision_scheduler
        self._server_recovery_policy = server_supervision_policy
        self._server_recovery_enabled = server_recovery_enabled
        self._server_recovery_cycle: AdbServerRecoveryCycle | None = None
        self._server_recovery_pending = False
        self._transport_list_watch_supervisor = transport_list_watch_supervisor
        self._transport_supervisor = transport_supervisor
        self._transport_supervision_policy = transport_supervision_policy

        self._runtime_lock = RLock()
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._started = False
        self._starting = False
        self._closed = False

    @property
    def transport_list(self) -> AdbTransportListSnapshotView:
        """Current server-bound transport-list observation exposed by this runtime."""

        return self._state.transport_list

    def dispatch_server_lifecycle_intent(
        self,
        intent: AdbServerLifecycleIntent,
    ) -> AdbServerLifecycleIntentResult:
        """Dispatch one complete server lifecycle transaction through the runtime facade."""

        return self._server_lifecycle.dispatch(intent)

    def provision_server(self) -> AdbServerProvisionTransactionResult:
        """Provision against the server state authoritative at execution time."""

        return self._server_lifecycle.provision()

    def retire_server(self) -> bool:
        """Retire the server lifetime authoritative at execution time."""

        return self._server_lifecycle.retire()

    def _provision_server_if_current(
        self,
        expected: AdbServerState,
    ) -> AdbServerProvisionTransactionResult | None:
        """Conditional Runtime entry used by T0-bound intent interpretation."""

        return self._server_lifecycle.provision_if_current(expected)

    def _retire_server_if_current(self, expected: AdbServerState) -> bool:
        """Conditional Runtime entry used by T0-bound intent interpretation."""

        return self._server_lifecycle.retire_if_current(expected)

    def _bootstrap_initial_server(self) -> None:
        """Provision and commit the initial server through the runtime lifecycle authority."""

        if self._state.server.current is not None:
            raise ValueError("bootstrap server provisioning requires empty runtime server state")

        result = self.provision_server()
        if isinstance(result, AdbServerProvisionDeferred):
            raise AdbServerControlError(
                f"initial ADB server provisioning deferred: {result.diagnostic}"
            )
        if isinstance(result, AdbServerProvisionFailed):
            raise AdbServerControlError(
                f"initial ADB server provisioning failed: {result.diagnostic}"
            )
        if not isinstance(result, AdbServerProvisionCommitted):
            raise TypeError("server lifecycle facade returned an unsupported provision result")
        if self._state.server.current != result.server:
            raise AdbServerControlError(
                "initial ADB server provisioning did not commit its server lifetime"
            )

    def _replace_server_provisioner(
        self,
        provisioner: AdbServerProvisioner,
    ) -> None:
        """Replace the pre-start provisioning policy while preserving runtime ownership."""

        if not isinstance(provisioner, AdbServerProvisioner):
            raise TypeError("provisioner must be AdbServerProvisioner")
        with self._runtime_lock:
            if self._closed or self._started or self._starting:
                raise RuntimeError(
                    "ADB server provisioner can only be replaced before runtime start"
                )
            self._server_lifecycle.replace_provisioner(provisioner)

    def _install_auxiliary_supervisors(
        self,
        transport_list_watch_supervisor: AdbTransportListWatchSupervisor | None,
        transport_supervisor: AdbConfiguredTransportSupervisor | None,
    ) -> None:
        """Install bootstrap-composed transport-list watch and configured-transport supervisors
        before start.
        """

        if transport_list_watch_supervisor is not None and not isinstance(
            transport_list_watch_supervisor, AdbTransportListWatchSupervisor
        ):
            raise TypeError(
                "transport_list_watch_supervisor must be "
                "AdbTransportListWatchSupervisor or None"
            )
        if transport_supervisor is not None and not isinstance(
            transport_supervisor, AdbConfiguredTransportSupervisor
        ):
            raise TypeError(
                "transport_supervisor must be AdbConfiguredTransportSupervisor or None"
            )
        if (
            transport_list_watch_supervisor is not None
            and transport_list_watch_supervisor.server_state is not self._state.server
        ):
            raise ValueError("transport-list watch supervisor must share the runtime server state")
        if (
            transport_list_watch_supervisor is not None
            and transport_list_watch_supervisor.transport_list_state is not self._state.transport_list
        ):
            raise ValueError(
                "transport-list watch supervisor must share the runtime transport-list state"
            )
        if (
            transport_supervisor is not None
            and transport_supervisor.server_state is not self._state.server
        ):
            raise ValueError("transport supervisor must share the runtime server state")
        if (
            transport_supervisor is not None
            and transport_supervisor.transport_list_state is not self._state.transport_list
        ):
            raise ValueError("transport supervisor must share the runtime transport-list state")

        with self._runtime_lock:
            if self._closed or self._started or self._starting:
                raise RuntimeError(
                    "runtime supervisors can only be installed before runtime start"
                )
            if (
                self._transport_list_watch_supervisor is not None
                or self._transport_supervisor is not None
            ):
                raise RuntimeError("runtime auxiliary supervisors are already configured")
            self._transport_list_watch_supervisor = transport_list_watch_supervisor
            self._transport_supervisor = transport_supervisor

    @property
    def started(self) -> bool:
        with self._runtime_lock:
            return self._started

    @property
    def closed(self) -> bool:
        with self._runtime_lock:
            return self._closed

    def start(self) -> None:
        """Start the bootstrap-composed runtime supervision graph."""

        with self._runtime_lock:
            if self._closed:
                raise RuntimeError("ADB runtime is closed")
            if self._started or self._starting:
                raise RuntimeError("ADB runtime is already started")
            self._starting = True

        watch_started = False
        transport_started = False
        subscription_tokens: list[EventSubscriptionToken] = []
        try:
            transport_supervisor = self._transport_supervisor
            if transport_supervisor is not None:
                transport_started = True
                transport_supervisor.start()

            event_bus = self._event_bus
            if event_bus is not None:
                # Runtime owns server reconciliation; recovery is a bounded child cycle started only
                # after the authoritative retirement transaction commits.
                subscription_tokens.append(
                    event_bus.subscribe(
                        AdbServerReconciliationRequested,
                        self._on_server_reconciliation_requested,
                    )
                )
                if transport_supervisor is not None:
                    # Rebind configured transports before a successor watch can publish observations.
                    subscription_tokens.append(
                        event_bus.subscribe(AdbServerRetired, self._on_server_retired)
                    )
                    subscription_tokens.append(
                        event_bus.subscribe(AdbServerRecovered, self._on_server_recovered)
                    )

            transport_list_watch_supervisor = self._transport_list_watch_supervisor
            if transport_list_watch_supervisor is not None:
                watch_started = True
                transport_list_watch_supervisor.start()
        except BaseException:
            if self._event_bus is not None:
                for token in subscription_tokens:
                    self._event_bus.unsubscribe(token)
            if watch_started and self._transport_list_watch_supervisor is not None:
                self._transport_list_watch_supervisor.close()
            if transport_started and self._transport_supervisor is not None:
                self._transport_supervisor.close()
            with self._runtime_lock:
                recovery_cycle = self._server_recovery_cycle
                self._server_recovery_cycle = None
                self._server_recovery_pending = False
                self._starting = False
            if recovery_cycle is not None:
                recovery_cycle.close()
            raise

        subscriptions = tuple(subscription_tokens)

        with self._runtime_lock:
            self._subscriptions = subscriptions
            self._started = True
            self._starting = False

    def close(self) -> None:
        """Release runtime infrastructure while preserving the current healthy server."""

        with self._runtime_lock:
            if self._closed:
                return
            self._closed = True
            self._started = False
            subscriptions = self._subscriptions
            self._subscriptions = ()
            recovery_cycle = self._server_recovery_cycle
            self._server_recovery_cycle = None
            self._server_recovery_pending = False

        event_bus = self._event_bus
        if event_bus is not None:
            for token in subscriptions:
                event_bus.unsubscribe(token)

        # Stop producers before consumers, then stop any bounded server-recovery work. None of
        # these close operations terminate the current healthy server lifetime.
        if self._transport_list_watch_supervisor is not None:
            self._transport_list_watch_supervisor.close()
        if self._transport_supervisor is not None:
            self._transport_supervisor.close()
        if recovery_cycle is not None:
            recovery_cycle.close()
        self._close_transport_registrations()

    def _register_transport(
        self,
        configuration: AdbConfiguredTransport,
        policy: AdbConfiguredTransportSupervisionPolicy | None,
    ) -> None:
        self._require_started()
        supervisor = self._require_transport_supervisor()
        supervisor.register(
            configuration,
            self._transport_supervision_policy if policy is None else policy,
        )

    def _unregister_transport(self, configuration: AdbConfiguredTransport) -> None:
        self._require_started()
        supervisor = self._require_transport_supervisor()
        if not supervisor.unregister(configuration):
            raise RuntimeError("configured transport registration disappeared from supervision")

    def _on_server_reconciliation_requested(
        self,
        event: AdbServerReconciliationRequested,
    ) -> None:
        """Commit one requested retirement, then start bounded recovery for that deactivation."""

        with self._runtime_lock:
            if self._closed or not (self._started or self._starting):
                return
            event_bus = self._event_bus
        if event_bus is None:
            return

        reconciliation = self._server_lifecycle.dispatch(AdbServerReconcileIntent(event))
        if reconciliation is None:
            return

        try:
            event_bus.publish(reconciliation.retired)
            event_bus.publish(reconciliation.lost)
        finally:
            self._request_server_recovery()

    def _request_server_recovery(self) -> None:
        """Start one recovery cycle, or remember a newer deactivation while one is active."""

        with self._runtime_lock:
            if (
                self._closed
                or not (self._started or self._starting)
                or not self._server_recovery_enabled
                or self._server_recovery_scheduler is None
                or self._event_bus is None
            ):
                return

            current = self._server_recovery_cycle
            if current is not None and not current.finished:
                self._server_recovery_pending = True
                return

            self._server_recovery_pending = False
            cycle = AdbServerRecoveryCycle(
                self._server_lifecycle,
                self._event_bus,
                self._server_recovery_scheduler,
                self._server_recovery_policy,
                _on_finished=self._on_server_recovery_finished,
            )
            self._server_recovery_cycle = cycle

        try:
            cycle.start()
        except BaseException:
            with self._runtime_lock:
                if self._server_recovery_cycle is cycle:
                    self._server_recovery_cycle = None
                    self._server_recovery_pending = False
            cycle.close()
            raise

    def _on_server_recovery_finished(self, cycle: AdbServerRecoveryCycle) -> None:
        """Release a finished cycle and cover a deactivation that raced its final ensure."""

        restart = False
        with self._runtime_lock:
            if self._server_recovery_cycle is not cycle:
                return
            self._server_recovery_cycle = None
            pending = self._server_recovery_pending
            self._server_recovery_pending = False
            if (
                pending
                and not self._closed
                and (self._started or self._starting)
                and self._server_recovery_enabled
                and self._server_recovery_scheduler is not None
            ):
                # If the finishing cycle's final ensure already covered the newer deactivation,
                # authoritative state is active and no successor cycle is needed.
                restart = not self._state.observe_server().active

        if restart:
            self._request_server_recovery()

    def _on_server_retired(self, _event: AdbServerRetired) -> None:
        supervisor = self._transport_supervisor
        if supervisor is not None:
            supervisor.reconcile()

    def _on_server_recovered(self, _event: AdbServerRecovered) -> None:
        supervisor = self._transport_supervisor
        if supervisor is not None:
            supervisor.reconcile()

    def _require_started(self) -> None:
        with self._runtime_lock:
            if self._closed:
                raise RuntimeError("ADB runtime is closed")
            if not self._started:
                raise RuntimeError("ADB runtime must be started first")

    def _require_transport_supervisor(self) -> AdbConfiguredTransportSupervisor:
        supervisor = self._transport_supervisor
        if supervisor is None:
            raise RuntimeError("configured transport supervision is not configured")
        return supervisor


def _is_event_bus(value: object) -> bool:
    return (
        callable(getattr(value, "publish", None))
        and callable(getattr(value, "subscribe", None))
        and callable(getattr(value, "unsubscribe", None))
    )


__all__ = ["AdbRuntime"]
