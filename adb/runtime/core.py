from __future__ import annotations

from threading import RLock

from adb.epoch import EpochIssuer
from adb.runtime.managed import AdbManagedRuntime
from adb.server.epoch import ServerEpoch
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
)
from adb.server.lifecycle.transaction import (
    AdbServerProvisionCommitted,
    AdbServerProvisionTransactionResult,
)
from adb.server.lifecycle.supervision.policy import AdbServerSupervisionPolicy
from adb.server.lifecycle.supervision.supervisor import AdbServerSupervisor
from adb.server.signal import AdbServerRecovered, AdbServerRetired
from adb.runtime.server_lifecycle import AdbServerLifecycleRuntimeFacade
from adb.runtime.state import AdbRuntimeState
from adb.server.state import AdbServerStateSnapshot
from adb.transport.configuration import AdbConfiguredTransport
from adb.tracking.snapshot.state import AdbDevicesSnapshotView
from adb.tracking.supervision.supervisor import AdbDevicesTrackingSupervisor
from adb.transport.lifecycle.supervision.policy import AdbConfiguredTransportSupervisionPolicy
from adb.transport.lifecycle.supervision.supervisor import AdbConfiguredTransportSupervisor
from eventing import EventBus, EventSubscriptionToken
from scheduling import TemporalScheduler


class AdbRuntime(AdbManagedRuntime):
    """Own the composed ADB capability graph and its authoritative server and device state."""

    def __init__(
        self,
        state: AdbRuntimeState,
        *,
        server_epoch_issuer: EpochIssuer[ServerEpoch],
        server_provisioner: AdbServerProvisioner,
        server_retirer: AdbServerRetirer,
        event_bus: EventBus | None = None,
        server_supervision_scheduler: TemporalScheduler[object] | None = None,
        server_supervision_policy: AdbServerSupervisionPolicy | None = None,
        server_recovery_enabled: bool = True,
        tracking_supervisor: AdbDevicesTrackingSupervisor | None = None,
        transport_supervisor: AdbConfiguredTransportSupervisor | None = None,
        transport_supervision_policy: AdbConfiguredTransportSupervisionPolicy | None = None,
        _bootstrap_server: bool = False,
    ) -> None:
        if not isinstance(state, AdbRuntimeState):
            raise TypeError("state must be AdbRuntimeState")
        if not isinstance(server_epoch_issuer, EpochIssuer):
            raise TypeError("server_epoch_issuer must satisfy EpochIssuer")
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
            server_supervision_policy = AdbServerSupervisionPolicy()
        if not isinstance(server_supervision_policy, AdbServerSupervisionPolicy):
            raise TypeError(
                "server_supervision_policy must be AdbServerSupervisionPolicy or None"
            )
        if not isinstance(server_recovery_enabled, bool):
            raise TypeError("server_recovery_enabled must be bool")
        if not isinstance(_bootstrap_server, bool):
            raise TypeError("_bootstrap_server must be bool")
        if tracking_supervisor is not None and not isinstance(
            tracking_supervisor, AdbDevicesTrackingSupervisor
        ):
            raise TypeError("tracking_supervisor must be AdbDevicesTrackingSupervisor or None")
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
                tracking_supervisor,
                transport_supervisor,
            )
        ) and event_bus is None:
            raise ValueError("supervised runtime components require an event bus")
        if (
            tracking_supervisor is not None
            and tracking_supervisor.server_state is not state.server
        ):
            raise ValueError("tracking supervisor must share the runtime server state")
        if tracking_supervisor is not None and tracking_supervisor.devices is not state.devices:
            raise ValueError("tracking supervisor must share the runtime tracked-devices state")
        if (
            transport_supervisor is not None
            and transport_supervisor.server_state is not state.server
        ):
            raise ValueError("transport supervisor must share the runtime server state")
        if transport_supervisor is not None and transport_supervisor.devices is not state.devices:
            raise ValueError("transport supervisor must share the runtime tracked-devices state")

        super().__init__(state.server)
        self._state = state
        self._event_bus = event_bus
        self._server_lifecycle = AdbServerLifecycleRuntimeFacade(
            state,
            server_epoch_issuer=server_epoch_issuer,
            provisioner=server_provisioner,
            retirer=server_retirer,
        )
        if _bootstrap_server:
            self._bootstrap_initial_server()

        self._server_supervisor: AdbServerSupervisor | None = None
        if server_supervision_scheduler is not None:
            if event_bus is None:
                raise RuntimeError("validated server supervision requires an event bus")
            self._server_supervisor = AdbServerSupervisor(
                self,
                event_bus=event_bus,
                scheduler=server_supervision_scheduler,
                policy=server_supervision_policy,
                recovery_enabled=server_recovery_enabled,
            )
        self._tracking_supervisor = tracking_supervisor
        self._transport_supervisor = transport_supervisor
        self._transport_supervision_policy = transport_supervision_policy

        self._runtime_lock = RLock()
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._started = False
        self._starting = False
        self._closed = False

    @property
    def devices(self) -> AdbDevicesSnapshotView:
        """Current server-bound tracked-devices observation exposed by this runtime."""

        return self._state.devices

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
        expected: AdbServerStateSnapshot,
    ) -> AdbServerProvisionTransactionResult | None:
        """Conditional Runtime entry used by T0-bound intent interpretation."""

        return self._server_lifecycle.provision_if_current(expected)

    def _retire_server_if_current(self, expected: AdbServerStateSnapshot) -> bool:
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
        tracking_supervisor: AdbDevicesTrackingSupervisor | None,
        transport_supervisor: AdbConfiguredTransportSupervisor | None,
    ) -> None:
        """Install bootstrap-composed tracking and transport supervisors before start."""

        if tracking_supervisor is not None and not isinstance(
            tracking_supervisor, AdbDevicesTrackingSupervisor
        ):
            raise TypeError("tracking_supervisor must be AdbDevicesTrackingSupervisor or None")
        if transport_supervisor is not None and not isinstance(
            transport_supervisor, AdbConfiguredTransportSupervisor
        ):
            raise TypeError(
                "transport_supervisor must be AdbConfiguredTransportSupervisor or None"
            )
        if (
            tracking_supervisor is not None
            and tracking_supervisor.server_state is not self._state.server
        ):
            raise ValueError("tracking supervisor must share the runtime server state")
        if (
            tracking_supervisor is not None
            and tracking_supervisor.devices is not self._state.devices
        ):
            raise ValueError("tracking supervisor must share the runtime tracked-devices state")
        if (
            transport_supervisor is not None
            and transport_supervisor.server_state is not self._state.server
        ):
            raise ValueError("transport supervisor must share the runtime server state")
        if (
            transport_supervisor is not None
            and transport_supervisor.devices is not self._state.devices
        ):
            raise ValueError("transport supervisor must share the runtime tracked-devices state")

        with self._runtime_lock:
            if self._closed or self._started or self._starting:
                raise RuntimeError(
                    "runtime supervisors can only be installed before runtime start"
                )
            if self._tracking_supervisor is not None or self._transport_supervisor is not None:
                raise RuntimeError("runtime auxiliary supervisors are already configured")
            self._tracking_supervisor = tracking_supervisor
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

        server_started = False
        tracking_started = False
        transport_started = False
        subscriptions: tuple[EventSubscriptionToken, ...] = ()
        try:
            transport_supervisor = self._transport_supervisor
            if transport_supervisor is not None:
                transport_started = True
                transport_supervisor.start()

            event_bus = self._event_bus
            if event_bus is not None and transport_supervisor is not None:
                # Rebind configured transports before a successor tracker can publish observations.
                subscriptions = (
                    event_bus.subscribe(AdbServerRetired, self._on_server_retired),
                    event_bus.subscribe(AdbServerRecovered, self._on_server_recovered),
                )

            server_supervisor = self._server_supervisor
            if server_supervisor is not None:
                server_started = True
                server_supervisor.start()

            tracking_supervisor = self._tracking_supervisor
            if tracking_supervisor is not None:
                tracking_started = True
                tracking_supervisor.start()
        except BaseException:
            if self._event_bus is not None:
                for token in subscriptions:
                    self._event_bus.unsubscribe(token)
            if tracking_started and self._tracking_supervisor is not None:
                self._tracking_supervisor.close()
            if transport_started and self._transport_supervisor is not None:
                self._transport_supervisor.close()
            if server_started and self._server_supervisor is not None:
                self._server_supervisor.close()
            with self._runtime_lock:
                self._starting = False
            raise

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

        event_bus = self._event_bus
        if event_bus is not None:
            for token in subscriptions:
                event_bus.unsubscribe(token)

        # Stop producers before consumers, then stop server-recovery automation.  None of these
        # close operations terminate the current healthy server lifetime.
        if self._tracking_supervisor is not None:
            self._tracking_supervisor.close()
        if self._transport_supervisor is not None:
            self._transport_supervisor.close()
        if self._server_supervisor is not None:
            self._server_supervisor.close()
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
