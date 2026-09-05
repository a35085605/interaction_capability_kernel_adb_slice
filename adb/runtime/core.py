from __future__ import annotations

from collections.abc import Callable
from threading import RLock

from adb.runtime.managed import AdbManagedRuntime
from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle.backend import (
    AdbServerBackend,
    AdbServerBackendAcquireDeferred,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAlreadyAcquired,
    AdbServerBackendEventPublisherBinding,
)
from adb.server.lifecycle.errors import (
    AdbServerBootstrapError,
    AdbServerLifecycleConsistencyError,
)
from adb.server.lifecycle.supervision.policy import AdbServerRecoveryPolicy
from adb.server.lifecycle.coordinator import (
    AdbServerAlreadyActive,
    AdbServerLifecycleCoordinator,
    AdbServerProvisionResult,
)
from adb.server.lifecycle.provision import (
    AdbServerProvisionActivated,
    AdbServerProvisionActivationConflict,
    classify_provision_result,
)
from adb.server.lifecycle.supervision.supervisor import AdbServerSupervisor
from adb.server.state import AdbServerActivated, AdbServerDeactivated
from adb.runtime.state import AdbRuntimeState
from adb.transport.configuration import AdbConfiguredTransport
from adb.transport_list.coordinator import (
    AdbTransportListCoordinatedObservationResult,
    AdbTransportListCoordinator,
)
from adb.transport_list.identity import AdbTransportListIdentityIssuer
from adb.transport_list.reader import AdbTransportListReader
from adb.transport_list.state import AdbTransportListStateView
from adb.transport_list.watch.supervision.policy import (
    AdbTransportListWatchSupervisionPolicy,
)
from adb.transport_list.watch.watcher import AdbTransportListWatcher
from adb.transport_list.watch.supervision.supervisor import AdbTransportListWatchSupervisor
from adb.transport.lifecycle.supervision.policy import AdbConfiguredTransportSupervisionPolicy
from adb.transport.lifecycle.supervision.supervisor import AdbConfiguredTransportSupervisor
from eventing import EventBus, EventSubscriptionToken
from scheduling import TemporalScheduler


_TransportListWatcherFactory = Callable[[TcpAddress, float], AdbTransportListWatcher]


class AdbRuntime(AdbManagedRuntime):
    """Own the composed ADB capability graph and its authoritative server and transport-list
    state.
    """

    def __init__(
        self,
        state: AdbRuntimeState,
        *,
        server_backend: AdbServerBackend,
        server_endpoint_constraint: AdbServerEndpoint | None = None,
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
        if not isinstance(server_backend, AdbServerBackend):
            raise TypeError("server_backend must satisfy AdbServerBackend")
        if server_endpoint_constraint is not None and not isinstance(
            server_endpoint_constraint, TcpAddress
        ):
            raise TypeError("server_endpoint_constraint must be TcpAddress or None")
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

        if event_bus is not None and isinstance(
            server_backend,
            AdbServerBackendEventPublisherBinding,
        ):
            server_backend.bind_event_publisher(event_bus)

        super().__init__(state.server)
        self._state = state
        self._authority_lock = RLock()
        self._transport_list_identity_issuer = AdbTransportListIdentityIssuer(
            after=state.transport_list.identity
        )
        self._server_lifecycle = AdbServerLifecycleCoordinator(
            state.server,
            backend=server_backend,
            endpoint_constraint=server_endpoint_constraint,
            publisher=event_bus,
            authority_lock=self._authority_lock,
        )
        self._transport_list_coordinator = AdbTransportListCoordinator(
            state.transport_list,
            state.server,
            self._transport_list_identity_issuer,
            publisher=event_bus,
            authority_lock=self._authority_lock,
        )
        # Preserve the former private name while the coordinator grows beyond observation-only use.
        self._transport_list_observation = self._transport_list_coordinator
        if _bootstrap_server:
            self._bootstrap_initial_server()

        self._transport_list_watch_supervisor = transport_list_watch_supervisor
        self._transport_supervisor = transport_supervisor
        self._transport_supervision_policy = transport_supervision_policy
        self._server_supervisor = AdbServerSupervisor(
            self._server_lifecycle,
            event_bus=event_bus,
            scheduler=server_supervision_scheduler,
            policy=server_supervision_policy,
            recovery_enabled=server_recovery_enabled,
        )

        self._event_bus = event_bus
        self._server_activation_subscription: EventSubscriptionToken | None = None
        self._runtime_lock = RLock()
        self._started = False
        self._starting = False
        self._closed = False

    @property
    def transport_list(self) -> AdbTransportListStateView:
        """Current server-bound transport-list observation exposed by this runtime."""

        return self._state.transport_list

    def refresh_transport_list(
        self,
        reader: AdbTransportListReader,
    ) -> AdbTransportListCoordinatedObservationResult:
        """Delegate one authoritative one-shot refresh to the transport-list domain."""

        with self._runtime_lock:
            if self._closed:
                raise RuntimeError("ADB runtime is closed")
        return self._transport_list_coordinator.refresh(reader)

    def _build_transport_list_watch_supervisor(
        self,
        policy: AdbTransportListWatchSupervisionPolicy,
        *,
        _watcher_factory: _TransportListWatcherFactory,
    ) -> AdbTransportListWatchSupervisor:
        """Build the runtime-bound transport-list watch from runtime-owned identity authority."""

        if not isinstance(policy, AdbTransportListWatchSupervisionPolicy):
            raise TypeError("policy must be AdbTransportListWatchSupervisionPolicy")
        if not callable(_watcher_factory):
            raise TypeError("_watcher_factory must be callable")
        event_bus = self._event_bus
        if event_bus is None:
            raise RuntimeError("transport-list watch requires an event bus")
        server = self.server
        endpoint = self.current_endpoint
        if server is None or endpoint is None:
            raise RuntimeError("ADB runtime has no active server binding")
        return AdbTransportListWatchSupervisor(
            server,
            endpoint,
            event_bus,
            policy,
            server_state=self._state.server,
            transport_list_observation_coordinator=self._transport_list_coordinator,
            _watcher_factory=_watcher_factory,
        )

    def provision_server(self) -> AdbServerProvisionResult:
        """Provision the authoritative server lifetime through runtime lifecycle ownership."""

        return self._server_lifecycle.provision()

    def retire_server(self) -> bool:
        """Retire the server lifetime authoritative at execution time."""

        return isinstance(self._server_lifecycle.retire(), AdbServerDeactivated)

    def _bootstrap_initial_server(self) -> None:
        """Provision the initial server through the runtime lifecycle authority."""

        if self._state.server.current is not None:
            raise ValueError("bootstrap server provisioning requires empty runtime server state")

        outcome = classify_provision_result(self.provision_server())
        if isinstance(outcome, AdbServerAlreadyActive):
            raise AdbServerLifecycleConsistencyError(
                "initial ADB server provisioning unexpectedly found an active server"
            )
        if isinstance(outcome, AdbServerBackendAlreadyAcquired):
            raise AdbServerBootstrapError(
                "initial ADB server provisioning found an already-acquired backend acquisition"
            )
        if isinstance(outcome, AdbServerBackendAcquireDeferred):
            raise AdbServerBootstrapError(
                f"initial ADB server provisioning deferred: {outcome.diagnostic}"
            )
        if isinstance(outcome, AdbServerBackendAcquireFailed):
            raise AdbServerBootstrapError(
                f"initial ADB server provisioning failed: {outcome.diagnostic}"
            )
        if isinstance(outcome, AdbServerProvisionActivationConflict):
            raise AdbServerLifecycleConsistencyError(
                "initial ADB server provisioning lost its authoritative-state activation fence"
            )
        if not isinstance(outcome, AdbServerProvisionActivated):
            raise TypeError("server lifecycle provision() returned an unsupported outcome")

    def _configure_server_endpoint_constraint(
        self,
        endpoint: AdbServerEndpoint | None,
    ) -> None:
        """Configure the endpoint constraint used for subsequent server recovery."""

        if endpoint is not None and not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress or None")
        with self._runtime_lock:
            if self._closed or self._started or self._starting:
                raise RuntimeError(
                    "ADB server endpoint constraint can only be configured before runtime start"
                )
            self._server_lifecycle.configure_endpoint_constraint(endpoint)

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
        server_started = False
        activation_subscription: EventSubscriptionToken | None = None
        try:
            transport_supervisor = self._transport_supervisor
            if transport_supervisor is not None:
                transport_started = True
                transport_supervisor.start()

            event_bus = self._event_bus
            if event_bus is not None:
                activation_subscription = event_bus.subscribe(
                    AdbServerActivated,
                    self._on_server_activated,
                )

            # Server supervision owns reconciliation subscriptions and bounded recovery. Runtime
            # reacts to committed server activations by reconciling server-bound dependents.
            server_started = True
            self._server_supervisor.start()

            transport_list_watch_supervisor = self._transport_list_watch_supervisor
            if transport_list_watch_supervisor is not None:
                watch_started = True
                transport_list_watch_supervisor.start()
        except BaseException:
            if activation_subscription is not None and self._event_bus is not None:
                self._event_bus.unsubscribe(activation_subscription)
            if server_started:
                self._server_supervisor.close()
            if watch_started and self._transport_list_watch_supervisor is not None:
                self._transport_list_watch_supervisor.close()
            if transport_started and self._transport_supervisor is not None:
                self._transport_supervisor.close()
            with self._runtime_lock:
                self._starting = False
            raise

        with self._runtime_lock:
            self._server_activation_subscription = activation_subscription
            self._started = True
            self._starting = False

    def close(self) -> None:
        """Release runtime infrastructure while preserving the current healthy server."""

        with self._runtime_lock:
            if self._closed:
                return
            self._closed = True
            self._started = False
            activation_subscription = self._server_activation_subscription
            self._server_activation_subscription = None

        # Disable activation reconciliation before dependent supervisors are closed. None of these
        # close operations retire the current healthy server.
        if activation_subscription is not None and self._event_bus is not None:
            self._event_bus.unsubscribe(activation_subscription)
        self._server_supervisor.close()
        if self._transport_list_watch_supervisor is not None:
            self._transport_list_watch_supervisor.close()
        if self._transport_supervisor is not None:
            self._transport_supervisor.close()
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

    def _on_server_activated(self, event: AdbServerActivated) -> None:
        """Reconcile server-bound dependents after an authoritative activation commits."""

        if not isinstance(event, AdbServerActivated):
            raise TypeError("event must be AdbServerActivated")
        with self._runtime_lock:
            if self._closed or not (self._started or self._starting):
                return
        self._reconcile_server_dependents()

    def _reconcile_server_dependents(self) -> None:
        """Rebind runtime-owned server dependents to the current authoritative lifetime."""

        # Configured transports must reset their server-scoped projections before a successor
        # transport-list watch can publish observations for the new lifetime.
        transport_supervisor = self._transport_supervisor
        if transport_supervisor is not None:
            transport_supervisor.reconcile()

        watch_supervisor = self._transport_list_watch_supervisor
        if watch_supervisor is not None:
            watch_supervisor.reconcile()

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
