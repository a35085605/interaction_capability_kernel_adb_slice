from __future__ import annotations

from datetime import timedelta
from threading import RLock, Thread, current_thread

from adb.runtime.managed import AdbManagedRuntime
from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.failure import AdbServerLaunchFailure
from adb.server.lifecycle.backend import (
    AdbServerBackend,
    AdbServerBackendAcquireBlocked,
    AdbServerBackendAcquireFailed,
    AdbServerBackendAcquireInProgress,
    AdbServerBackendAcquireResult,
    AdbServerBackendAcquireSatisfied,
    AdbServerBackendAcquireSucceeded,
)
from adb.server.lifecycle.errors import (
    AdbServerBootstrapError,
    AdbServerLifecycleConsistencyError,
)
from adb.server.lifecycle.supervision.intent import AdbServerAcquireOnceIntent
from adb.server.lifecycle.supervision.policy import AdbServerRecoveryPolicy
from adb.server.lifecycle.supervision.recovery import (
    AdbServerRecovery,
    AdbServerRecoveryCompleted,
    AdbServerRecoveryExhaust,
)
from adb.server.signal import (
    AdbServerReconciliationRequested,
    AdbServerRecoveryId,
    AdbServerRecoveryExhausted,
    AdbServerRecoveryRetryDue,
)
from adb.runtime.server_lifecycle import AdbServerLifecycleRuntimeFacade
from adb.runtime.state import AdbRuntimeState
from adb.transport.configuration import AdbConfiguredTransport
from adb.tracking.snapshot.state import AdbTransportListSnapshotView
from adb.tracking.supervision.supervisor import AdbTransportListWatchSupervisor
from adb.transport.lifecycle.supervision.policy import AdbConfiguredTransportSupervisionPolicy
from adb.transport.lifecycle.supervision.supervisor import AdbConfiguredTransportSupervisor
from eventing import EventBus, EventSubscriptionToken
from scheduling import ScheduleToken, TemporalScheduler


class AdbRuntime(AdbManagedRuntime):
    """Own the composed ADB capability graph and its authoritative server and transport-list
    state.
    """

    def __init__(
        self,
        state: AdbRuntimeState,
        *,
        server_backend: AdbServerBackend,
        server_provision_endpoint: AdbServerEndpoint | None = None,
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
        if server_provision_endpoint is not None and not isinstance(
            server_provision_endpoint, TcpAddress
        ):
            raise TypeError("server_provision_endpoint must be TcpAddress or None")
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
            backend=server_backend,
            provision_endpoint=server_provision_endpoint,
        )
        if _bootstrap_server:
            self._bootstrap_initial_server()

        self._server_recovery_scheduler = server_supervision_scheduler
        self._server_recovery_policy = server_supervision_policy
        self._server_recovery_enabled = server_recovery_enabled
        self._server_recovery: AdbServerRecovery | None = None
        self._server_recovery_id: AdbServerRecoveryId | None = None
        self._server_recovery_intent: AdbServerAcquireOnceIntent | None = None
        self._server_recovery_retry_token: ScheduleToken | None = None
        self._server_recovery_attempt_threads: set[Thread] = set()
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

    def acquire_server_once(self) -> AdbServerBackendAcquireResult | None:
        """Execute at most one backend acquisition through runtime lifecycle ownership."""

        return self._server_lifecycle.acquire_once()

    def provision_server(self) -> AdbServerBackendAcquireResult | None:
        """Compatibility name for one runtime-owned server acquisition attempt."""

        return self.acquire_server_once()

    def retire_server(self) -> bool:
        """Retire the server lifetime authoritative at execution time."""

        return self._server_lifecycle.retire() is not None

    def _bootstrap_initial_server(self) -> None:
        """Acquire and commit the initial server through the runtime lifecycle authority."""

        if self._state.server.current is not None:
            raise ValueError("bootstrap server provisioning requires empty runtime server state")

        acquire = self.acquire_server_once()
        if acquire is None:
            raise AdbServerLifecycleConsistencyError(
                "initial ADB server acquisition did not execute"
            )
        if isinstance(acquire, AdbServerBackendAcquireInProgress):
            detail = acquire.diagnostic or "ADB server backend acquire is already in progress"
            raise AdbServerBootstrapError(f"initial ADB server acquisition deferred: {detail}")
        if isinstance(acquire, AdbServerBackendAcquireBlocked):
            raise AdbServerBootstrapError(
                f"initial ADB server acquisition deferred: {acquire.diagnostic}"
            )
        if isinstance(acquire, AdbServerBackendAcquireFailed):
            raise AdbServerBootstrapError(
                f"initial ADB server acquisition failed: {acquire.diagnostic}"
            )
        if not isinstance(
            acquire,
            (AdbServerBackendAcquireSucceeded, AdbServerBackendAcquireSatisfied),
        ):
            raise TypeError("server backend acquire() returned an unsupported result")

        state = self._state.observe_server()
        if not state.active or state.endpoint != acquire.endpoint:
            raise AdbServerLifecycleConsistencyError(
                "initial ADB server acquisition did not commit its server lifetime"
            )

    def _configure_server_provision_endpoint(
        self,
        endpoint: AdbServerEndpoint | None,
    ) -> None:
        """Configure the endpoint constraint used for subsequent server recovery."""

        if endpoint is not None and not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress or None")
        with self._runtime_lock:
            if self._closed or self._started or self._starting:
                raise RuntimeError(
                    "ADB server provision endpoint can only be configured before runtime start"
                )
            self._server_lifecycle.configure_provision_endpoint(endpoint)

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
                if self._server_recovery_scheduler is not None:
                    subscription_tokens.append(
                        event_bus.subscribe(
                            AdbServerRecoveryRetryDue,
                            self._on_server_recovery_retry_due,
                        )
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
                retry_token = self._server_recovery_retry_token
                self._server_recovery = None
                self._server_recovery_id = None
                self._server_recovery_intent = None
                self._server_recovery_retry_token = None
                self._server_recovery_pending = False
                attempt_threads = tuple(self._server_recovery_attempt_threads)
                self._starting = False
            if retry_token is not None and self._server_recovery_scheduler is not None:
                self._server_recovery_scheduler.cancel(retry_token)
            for thread in attempt_threads:
                if thread is not current_thread():
                    thread.join()
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
            retry_token = self._server_recovery_retry_token
            self._server_recovery = None
            self._server_recovery_id = None
            self._server_recovery_intent = None
            self._server_recovery_retry_token = None
            self._server_recovery_pending = False
            attempt_threads = tuple(self._server_recovery_attempt_threads)

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
        if retry_token is not None and self._server_recovery_scheduler is not None:
            self._server_recovery_scheduler.cancel(retry_token)
        for thread in attempt_threads:
            if thread is not current_thread():
                thread.join()
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

        deactivation = self._server_lifecycle.retire(expected_server=event.server)
        if deactivation is None:
            return

        try:
            self._reconcile_server_dependents()
        finally:
            self._request_server_recovery()

    def _request_server_recovery(self) -> None:
        """Start bounded acquisition recovery when authoritative runtime state requires it."""

        with self._runtime_lock:
            if (
                self._closed
                or not (self._started or self._starting)
                or not self._server_recovery_enabled
                or self._server_recovery_scheduler is None
                or self._event_bus is None
                or self._state.observe_server().active
            ):
                return

            if self._server_recovery is not None:
                self._server_recovery_pending = True
                return

            recovery = AdbServerRecovery(self._server_recovery_policy)
            recovery_id = AdbServerRecoveryId.new()
            intent = recovery.start()
            self._server_recovery = recovery
            self._server_recovery_id = recovery_id
            self._server_recovery_pending = False

        self._apply_server_recovery_intent(recovery, recovery_id, intent)

    def _apply_server_recovery_intent(
        self,
        recovery: AdbServerRecovery,
        recovery_id: AdbServerRecoveryId,
        intent: AdbServerAcquireOnceIntent,
    ) -> None:
        """Execute immediately or arrange the delay requested by low-level recovery."""

        with self._runtime_lock:
            if not self._is_current_server_recovery_locked(recovery, recovery_id):
                return
            runtime_already_active = self._state.observe_server().active
        if runtime_already_active:
            self._finish_server_recovery(recovery, recovery_id, completed=True)
            return

        if intent.delay_seconds > 0.0:
            scheduler = self._server_recovery_scheduler
            if scheduler is None:
                return
            token = scheduler.schedule_after(
                timedelta(seconds=intent.delay_seconds),
                AdbServerRecoveryRetryDue(recovery_id, intent.attempt_number),
            )
            with self._runtime_lock:
                if not self._is_current_server_recovery_locked(recovery, recovery_id):
                    scheduler.cancel(token)
                    return
                old_token = self._server_recovery_retry_token
                self._server_recovery_retry_token = token
                self._server_recovery_intent = intent
            if old_token is not None:
                scheduler.cancel(old_token)
            return

        self._launch_server_recovery_attempt(recovery, recovery_id, intent)

    def _on_server_recovery_retry_due(self, event: AdbServerRecoveryRetryDue) -> None:
        with self._runtime_lock:
            recovery = self._server_recovery
            recovery_id = self._server_recovery_id
            intent = self._server_recovery_intent
            if (
                recovery is None
                or recovery_id != event.recovery_id
                or intent is None
                or intent.attempt_number != event.attempt_number
                or not self._is_current_server_recovery_locked(recovery, recovery_id)
            ):
                return
            self._server_recovery_retry_token = None
            self._server_recovery_intent = None

        self._launch_server_recovery_attempt(recovery, recovery_id, intent)

    def _launch_server_recovery_attempt(
        self,
        recovery: AdbServerRecovery,
        recovery_id: AdbServerRecoveryId,
        intent: AdbServerAcquireOnceIntent,
    ) -> None:
        thread = Thread(
            target=self._run_server_recovery_attempt,
            args=(recovery, recovery_id, intent),
            name=(
                "adb-server-recovery-"
                f"{recovery_id.value[:12]}-{intent.attempt_number}"
            ),
            daemon=True,
        )
        with self._runtime_lock:
            if not self._is_current_server_recovery_locked(recovery, recovery_id):
                return
            self._server_recovery_attempt_threads.add(thread)
            try:
                thread.start()
            except BaseException:
                self._server_recovery_attempt_threads.discard(thread)
                raise

    def _run_server_recovery_attempt(
        self,
        recovery: AdbServerRecovery,
        recovery_id: AdbServerRecoveryId,
        intent: AdbServerAcquireOnceIntent,
    ) -> None:
        active_thread = current_thread()
        try:
            with self._runtime_lock:
                if not self._is_current_server_recovery_locked(recovery, recovery_id):
                    return
                if self._state.observe_server().active:
                    finish_without_attempt = True
                else:
                    finish_without_attempt = False

            if finish_without_attempt:
                self._finish_server_recovery(recovery, recovery_id, completed=True)
                return

            acquire = self._server_lifecycle.acquire_once()
            if acquire is None:
                # A concurrent runtime transition made acquisition unnecessary before the
                # lifecycle operation linearized. Domain state, not Recovery, decides completion.
                self._finish_server_recovery(recovery, recovery_id, completed=True)
                return

            decision = recovery.accept(acquire)
            if isinstance(decision, AdbServerAcquireOnceIntent):
                self._apply_server_recovery_intent(recovery, recovery_id, decision)
                return
            if isinstance(decision, AdbServerRecoveryCompleted):
                self._finish_server_recovery(recovery, recovery_id, completed=True)
                return
            if isinstance(decision, AdbServerRecoveryExhaust):
                event_bus = self._event_bus
                if event_bus is not None:
                    event_bus.publish(
                        AdbServerRecoveryExhausted(
                            recovery_id,
                            decision.attempts,
                            AdbServerLaunchFailure(decision.acquire.diagnostic),
                        )
                    )
                self._finish_server_recovery(recovery, recovery_id, completed=False)
                return
            raise TypeError("recovery returned an unsupported decision")
        finally:
            with self._runtime_lock:
                self._server_recovery_attempt_threads.discard(active_thread)

    def _finish_server_recovery(
        self,
        recovery: AdbServerRecovery,
        recovery_id: AdbServerRecoveryId,
        *,
        completed: bool,
    ) -> None:
        """Release one low-level recovery and let runtime state decide any successor work."""

        restart = False
        reconcile = False
        scheduler = self._server_recovery_scheduler
        with self._runtime_lock:
            if not self._is_current_server_recovery_locked(recovery, recovery_id):
                return
            retry_token = self._server_recovery_retry_token
            self._server_recovery = None
            self._server_recovery_id = None
            self._server_recovery_intent = None
            self._server_recovery_retry_token = None
            pending = self._server_recovery_pending
            self._server_recovery_pending = False

            if not self._closed and (self._started or self._starting):
                active = self._state.observe_server().active
                reconcile = completed and active
                # A completed acquisition task can still lose the runtime activation race. In that
                # case the Supervisor, not low-level Recovery, decides that a fresh recovery is
                # needed. Exhaustion only restarts for a newer deactivation recorded as pending.
                restart = (completed and not active) or (pending and not active)

        if retry_token is not None and scheduler is not None:
            scheduler.cancel(retry_token)
        if reconcile:
            self._reconcile_server_dependents()
        if restart:
            self._request_server_recovery()

    def _is_current_server_recovery_locked(
        self,
        recovery: AdbServerRecovery,
        recovery_id: AdbServerRecoveryId,
    ) -> bool:
        return (
            not self._closed
            and (self._started or self._starting)
            and self._server_recovery is recovery
            and self._server_recovery_id == recovery_id
        )

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
