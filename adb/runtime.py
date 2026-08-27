from __future__ import annotations

from threading import RLock

from adb.managed import AdbManagedRuntime
from adb.server.lifecycle.control.provisioner import AdbServerProvisioner
from adb.server.lifecycle.control.retirer import AdbServerRetirer
from adb.server.lifecycle.supervision.policy import AdbServerSupervisionPolicy
from adb.server.lifecycle.supervision.supervisor import AdbServerSupervisor
from adb.server.identity import AdbServer
from adb.server.signal import AdbServerRecovered, AdbServerRetired
from adb.server.state import AdbServerState
from adb.transport.configuration import AdbConfiguredTransport
from adb.tracking.snapshot.state import AdbDevicesSnapshotState, AdbDevicesSnapshotView
from adb.tracking.supervision.supervisor import AdbDevicesTrackingSupervisor
from adb.transport.lifecycle.supervision.policy import AdbConfiguredTransportSupervisionPolicy
from adb.transport.lifecycle.supervision.supervisor import AdbConfiguredTransportSupervisor
from eventing import EventBus, EventSubscriptionToken
from scheduling import TemporalScheduler


class AdbRuntime(AdbManagedRuntime):
    """Runtime ownership boundary for the composed ADB capability graph.

    Server and device state are authoritative; supervisors provide optional lifecycle automation.
    """

    def __init__(
        self,
        server_state: AdbServerState,
        snapshot_state: AdbDevicesSnapshotState,
        *,
        server_provisioner: AdbServerProvisioner,
        server_retirer: AdbServerRetirer,
        event_bus: EventBus | None = None,
        server_supervision_scheduler: TemporalScheduler[object] | None = None,
        server_supervision_policy: AdbServerSupervisionPolicy | None = None,
        server_recovery_enabled: bool = True,
        tracking_supervisor: AdbDevicesTrackingSupervisor | None = None,
        transport_supervisor: AdbConfiguredTransportSupervisor | None = None,
        transport_supervision_policy: AdbConfiguredTransportSupervisionPolicy | None = None,
    ) -> None:
        if not isinstance(server_state, AdbServerState):
            raise TypeError("server_state must be AdbServerState")
        if not isinstance(snapshot_state, AdbDevicesSnapshotState):
            raise TypeError("snapshot_state must be AdbDevicesSnapshotState")
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
            and tracking_supervisor.server_state is not server_state
        ):
            raise ValueError("tracking supervisor must share the runtime server state")
        if tracking_supervisor is not None and tracking_supervisor.devices is not snapshot_state:
            raise ValueError("tracking supervisor must share the runtime tracked-devices state")
        if (
            transport_supervisor is not None
            and transport_supervisor.server_state is not server_state
        ):
            raise ValueError("transport supervisor must share the runtime server state")
        if transport_supervisor is not None and transport_supervisor.devices is not snapshot_state:
            raise ValueError("transport supervisor must share the runtime tracked-devices state")

        super().__init__(server_state)
        self._snapshot_state = snapshot_state
        self._event_bus = event_bus
        self._server_supervisor: AdbServerSupervisor | None = None
        if server_supervision_scheduler is not None:
            if event_bus is None:
                raise RuntimeError("validated server supervision requires an event bus")
            self._server_supervisor = AdbServerSupervisor(
                server_state,
                provisioner=server_provisioner,
                retirer=server_retirer,
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
        """Read-only current server-bound tracked-devices observation for this runtime."""

        return self._snapshot_state

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
        """Release runtime infrastructure without stopping the current healthy server."""

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
