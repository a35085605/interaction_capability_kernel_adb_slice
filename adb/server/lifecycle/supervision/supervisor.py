from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from random import random
from threading import Lock, Thread, current_thread
from typing import Protocol, runtime_checkable

from adb.server.lifecycle.control.errors import (
    AdbServerNativeLifetimeBusyError,
    AdbServerNativeTerminationUnprovenError,
    AdbServerStartDeferredError,
    AdbServerStartError,
    AdbServerStopInProgressError,
)
from adb.server.lifecycle.supervision.policy import AdbServerSupervisionPolicy
from adb.server.failure import (
    AdbServerConnectionFailure,
    AdbServerLaunchFailure,
    AdbServerLivenessFailure,
    AdbServerNativeLifetimeBusyFailure,
    AdbServerNativeTerminationUnprovenFailure,
    AdbServerProcessExitedFailure,
    AdbServerStartDeferredFailure,
    AdbServerStopInProgressFailure,
)
from adb.server.identity import AdbServer
from adb.server.state import AdbServerState, AdbServerStateView
from adb.server.signal import (
    AdbServerRecoveryCycleId,
    AdbServerNativeTerminationCompleted,
    AdbServerNativeTerminationUnproven,
    AdbServerLost,
    AdbServerRecovered,
    AdbServerRetired,
    AdbServerReconciliationRequested,
    AdbServerRecoveryExhausted,
    AdbServerRecoveryRetryDue,
)
from eventing import EventBus, EventSubscriptionToken
from scheduling import ScheduleToken, TemporalScheduler


_RandomSource = Callable[[], float]
_ThreadFactory = Callable[..., Thread]


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


def _require_bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a bool")
    return value


@runtime_checkable
class _AdbServerLifecycleController(Protocol):
    """Supervisor-private contract for one inseparable server lifecycle owner."""

    def provision(self) -> AdbServer:
        ...

    def retire(self, server: AdbServer) -> None:
        ...


class AdbServerSupervisor:
    """Reconcile ADB server failures across successive server lifetimes.

    Current-lifetime truth is committed to ``AdbServerState``.  Whether a retired server may
    be automatically replaced is immutable composition-time configuration.
    """

    def __init__(
        self,
        server: AdbServer | AdbServerState,
        controller: _AdbServerLifecycleController,
        event_bus: EventBus,
        scheduler: TemporalScheduler[object],
        policy: AdbServerSupervisionPolicy,
        *,
        recovery_enabled: bool,
        _random: _RandomSource = random,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if isinstance(server, AdbServerState):
            server_state = server
        elif isinstance(server, AdbServer):
            server_state = AdbServerState(server)
        else:
            raise TypeError("server must be AdbServer or AdbServerState")
        if server_state.current is None:
            raise ValueError("server state must have an active initial server")
        if not isinstance(controller, _AdbServerLifecycleController):
            raise TypeError(
                "controller must satisfy the complete ADB server lifecycle contract"
            )
        if not callable(getattr(event_bus, "publish", None)) or not callable(
            getattr(event_bus, "subscribe", None)
        ) or not callable(getattr(event_bus, "unsubscribe", None)):
            raise TypeError("event_bus must satisfy EventBus")
        if not isinstance(scheduler, TemporalScheduler):
            raise TypeError("scheduler must satisfy TemporalScheduler")
        if not isinstance(policy, AdbServerSupervisionPolicy):
            raise TypeError("policy must be AdbServerSupervisionPolicy")
        recovery_enabled = _require_bool(
            recovery_enabled,
            field_name="recovery_enabled",
        )

        self._server_state = server_state
        self._bus = event_bus
        self._controller = controller
        self._scheduler = scheduler
        self._policy = policy
        self._random = _random
        self._thread_factory = _thread_factory

        self._lock = Lock()
        self._mutation_lock = Lock()
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._recovery_enabled = recovery_enabled
        self._cycle_id: AdbServerRecoveryCycleId | None = None
        self._retry_token: ScheduleToken | None = None
        self._launch_attempts = 0
        self._pending_retired_disposals: set[AdbServer] = set()
        self._attempt_threads: set[Thread] = set()
        self._closed = False

    @property
    def server(self) -> AdbServer | None:
        """Current server lifetime from the runtime authoritative state."""

        return self._server_state.current

    @property
    def server_state(self) -> AdbServerStateView:
        """Read-only authoritative state used by this supervisor."""

        return self._server_state

    @property
    def recovery_enabled(self) -> bool:
        with self._lock:
            return self._recovery_enabled

    def start(self) -> None:
        """Start reconciliation using the bootstrap-selected recovery configuration."""

        launch_cycle: AdbServerRecoveryCycleId | None = None
        with self._mutation_lock:
            with self._lock:
                self._require_open()
                if self._subscriptions:
                    raise RuntimeError("ADB server supervisor is already started")
                self._ensure_subscriptions_locked()
                if self.server is None and self._recovery_enabled:
                    launch_cycle = self._new_recovery_cycle_locked()
        if launch_cycle is not None:
            self._launch_recovery_attempt(launch_cycle, attempt_number=1)

    def reconcile(self, failure: AdbServerLivenessFailure) -> None:
        """Retire the current server after terminal liveness failure and reconcile desired state."""

        if not isinstance(
            failure,
            (AdbServerConnectionFailure, AdbServerProcessExitedFailure),
        ):
            raise TypeError(
                "failure must be AdbServerConnectionFailure or "
                "AdbServerProcessExitedFailure"
            )
        self._retire_current_and_maybe_recover(failure)

    def close(self) -> None:
        """Stop supervision without terminating the current healthy server."""

        with self._mutation_lock:
            with self._lock:
                if self._closed:
                    return
                self._closed = True
                subscriptions = self._subscriptions
                self._subscriptions = ()
                retry_token = self._invalidate_recovery_locked()
                pending_retired = tuple(self._pending_retired_disposals)
                self._pending_retired_disposals.clear()
                attempt_threads = tuple(self._attempt_threads)
        for token in subscriptions:
            self._bus.unsubscribe(token)
        if retry_token is not None:
            self._scheduler.cancel(retry_token)
        for server in pending_retired:
            self._dispose_retired_server(server)
        for thread in attempt_threads:
            if thread is not current_thread():
                thread.join()

    def _retire_current_and_maybe_recover(
        self,
        failure: AdbServerLivenessFailure,
    ) -> None:
        retired_server: AdbServer | None = None
        launch_cycle: AdbServerRecoveryCycleId | None = None
        with self._mutation_lock:
            with self._lock:
                self._require_open()
                server = self.server

            if server is not None:
                with self._lock:
                    if not self._server_state.retire(server):
                        return
                    retired_server = server
                    self._pending_retired_disposals.add(server)
                    if self._recovery_enabled and self._cycle_id is None:
                        launch_cycle = self._new_recovery_cycle_locked()

        if retired_server is None:
            return

        # Domain retirement is authoritative immediately.  Native termination is independent and
        # may race successor provisioning; consumers must use state.current plus epoch rather than
        # native-termination signal order.
        try:
            self._bus.publish(AdbServerRetired(retired_server))
            self._bus.publish(AdbServerLost(retired_server, failure))
        finally:
            self._launch_retired_disposal(retired_server)
            if launch_cycle is not None:
                self._launch_recovery_attempt(launch_cycle, attempt_number=1)

    def _launch_retired_disposal(self, server: AdbServer) -> None:
        with self._mutation_lock:
            with self._lock:
                if server not in self._pending_retired_disposals:
                    return
                thread = self._thread_factory(
                    target=self._run_retired_disposal,
                    args=(server,),
                    name=(
                        "adb-server-close-"
                        f"{server.endpoint.host}-{server.endpoint.port}-{server.epoch}"
                    ),
                )
                self._attempt_threads.add(thread)
                try:
                    thread.start()
                except BaseException:
                    self._attempt_threads.discard(thread)
                    raise
                else:
                    self._pending_retired_disposals.remove(server)

    def _run_retired_disposal(self, server: AdbServer) -> None:
        active = current_thread()
        try:
            self._dispose_retired_server(server)
        finally:
            with self._lock:
                self._attempt_threads.discard(active)

    def _dispose_retired_server(self, server: AdbServer) -> None:
        try:
            self._controller.retire(server)
        except AdbServerNativeTerminationUnprovenError as exc:
            # Only the backend may establish this terminal native fact.  Do not manufacture
            # UNPROVEN from generic controller/ownership errors.
            self._bus.publish(
                AdbServerNativeTerminationUnproven(
                    server,
                    AdbServerNativeTerminationUnprovenFailure(str(exc)),
                )
            )
            return

        self._bus.publish(AdbServerNativeTerminationCompleted(server))

    def _launch_recovery_attempt(
        self,
        cycle_id: AdbServerRecoveryCycleId,
        attempt_number: int,
    ) -> None:
        thread = self._thread_factory(
            target=self._run_recovery_attempt,
            args=(cycle_id, attempt_number),
            name=(
                "adb-server-recovery-"
                f"{cycle_id.value[:12]}-{attempt_number}"
            ),
        )
        with self._lock:
            if not self._recovery_is_current_locked(cycle_id):
                return
            self._attempt_threads.add(thread)
            try:
                thread.start()
            except BaseException:
                self._attempt_threads.discard(thread)
                raise

    def _provision_server(self) -> AdbServer:
        server = self._controller.provision()
        if not isinstance(server, AdbServer):
            raise TypeError("server controller provision() must return AdbServer")
        return server

    def _run_recovery_attempt(
        self,
        cycle_id: AdbServerRecoveryCycleId,
        attempt_number: int,
    ) -> None:
        active = current_thread()
        deferred_failure: AdbServerStartDeferredFailure | None = None
        unproven_failure: AdbServerNativeTerminationUnprovenFailure | None = None
        launch_failure: AdbServerLaunchFailure | None = None
        launch_attempts = 0
        recovered_event: AdbServerRecovered | None = None
        retry_token: ScheduleToken | None = None
        try:
            with self._mutation_lock:
                with self._lock:
                    if not self._recovery_is_current_locked(cycle_id):
                        return
                try:
                    recovered = self._provision_server()
                except AdbServerStopInProgressError as exc:
                    deferred_failure = AdbServerStopInProgressFailure(str(exc))
                except AdbServerNativeLifetimeBusyError as exc:
                    deferred_failure = AdbServerNativeLifetimeBusyFailure(str(exc))
                except AdbServerNativeTerminationUnprovenError as exc:
                    unproven_failure = AdbServerNativeTerminationUnprovenFailure(str(exc))
                except AdbServerStartDeferredError as exc:
                    deferred_failure = AdbServerStartDeferredFailure(str(exc))
                except AdbServerStartError as exc:
                    launch_failure = AdbServerLaunchFailure(str(exc))
                    with self._lock:
                        if self._recovery_is_current_locked(cycle_id):
                            self._launch_attempts += 1
                            launch_attempts = self._launch_attempts
                else:
                    with self._lock:
                        if not self._recovery_is_current_locked(cycle_id):
                            return
                        if not self._server_state.activate(recovered):
                            return
                        retry_token = self._retry_token
                        self._retry_token = None
                        self._launch_attempts = 0
                        self._cycle_id = None
                        recovered_event = AdbServerRecovered(recovered)

            if recovered_event is not None:
                if retry_token is not None:
                    self._scheduler.cancel(retry_token)
                self._bus.publish(recovered_event)
                return

            if unproven_failure is not None:
                # NATIVE_TERMINATION_UNPROVEN is terminal for this backend scope.  The native
                # termination signal is the external-intervention hook; do not spin recovery.
                self._end_recovery_cycle(cycle_id)
                return

            if deferred_failure is not None:
                with self._lock:
                    if not self._recovery_is_current_locked(cycle_id):
                        return
                self._schedule_deferred_retry(
                    cycle_id,
                    attempt_number,
                    deferred_failure,
                )
                return

            if launch_failure is not None:
                with self._lock:
                    if not self._recovery_is_current_locked(cycle_id):
                        return
                self._schedule_launch_retry_or_exhaust(
                    cycle_id,
                    attempt_number,
                    launch_attempts,
                    launch_failure,
                )
        finally:
            with self._lock:
                self._attempt_threads.discard(active)

    def _schedule_deferred_retry(
        self,
        cycle_id: AdbServerRecoveryCycleId,
        attempt_number: int,
        _failure: AdbServerStartDeferredFailure,
    ) -> None:
        # Native convergence contention is expected and does not consume launch-attempt budget.
        self._schedule_retry(
            cycle_id,
            attempt_number + 1,
            self._policy.deferred_retry_seconds,
        )

    def _schedule_launch_retry_or_exhaust(
        self,
        cycle_id: AdbServerRecoveryCycleId,
        attempt_number: int,
        launch_attempts: int,
        failure: AdbServerLaunchFailure,
    ) -> None:
        max_attempts = self._policy.max_attempts
        if max_attempts is not None and launch_attempts >= max_attempts:
            self._end_recovery_cycle(cycle_id)
            self._bus.publish(
                AdbServerRecoveryExhausted(
                    cycle_id,
                    launch_attempts,
                    failure,
                )
            )
            return

        self._schedule_retry(
            cycle_id,
            attempt_number + 1,
            self._retry_delay(launch_attempts),
        )

    def _schedule_retry(
        self,
        cycle_id: AdbServerRecoveryCycleId,
        next_attempt_number: int,
        delay_seconds: float,
    ) -> None:
        retry_event = AdbServerRecoveryRetryDue(
            cycle_id,
            next_attempt_number,
        )
        token = self._scheduler.schedule_after(
            timedelta(seconds=delay_seconds),
            retry_event,
        )
        with self._lock:
            if not self._recovery_is_current_locked(cycle_id):
                self._scheduler.cancel(token)
                return
            old_token = self._retry_token
            self._retry_token = token
        if old_token is not None:
            self._scheduler.cancel(old_token)

    def _ensure_subscriptions_locked(self) -> None:
        if self._subscriptions:
            return
        reconciliation_subscription = self._bus.subscribe(
            AdbServerReconciliationRequested,
            self._on_reconciliation_requested,
        )
        retry_subscription = self._bus.subscribe(
            AdbServerRecoveryRetryDue,
            self._on_retry_due,
        )
        self._subscriptions = (reconciliation_subscription, retry_subscription)

    def _on_reconciliation_requested(
        self,
        event: AdbServerReconciliationRequested,
    ) -> None:
        with self._lock:
            server = self.server
            if server is None or server != event.server:
                return
        self._retire_current_and_maybe_recover(event.failure)

    def _on_retry_due(self, event: AdbServerRecoveryRetryDue) -> None:
        with self._lock:
            if not self._recovery_is_current_locked(event.cycle_id):
                return
            self._retry_token = None
        self._launch_recovery_attempt(event.cycle_id, event.attempt_number)

    def _new_recovery_cycle_locked(self) -> AdbServerRecoveryCycleId:
        if self._cycle_id is not None:
            return self._cycle_id
        cycle_id = AdbServerRecoveryCycleId.new()
        self._cycle_id = cycle_id
        self._launch_attempts = 0
        return cycle_id

    def _end_recovery_cycle(self, cycle_id: AdbServerRecoveryCycleId) -> None:
        with self._lock:
            if self._cycle_id != cycle_id:
                return
            retry_token = self._retry_token
            self._retry_token = None
            self._launch_attempts = 0
            self._cycle_id = None
        if retry_token is not None:
            self._scheduler.cancel(retry_token)

    def _invalidate_recovery_locked(self) -> ScheduleToken | None:
        retry_token = self._retry_token
        self._retry_token = None
        self._launch_attempts = 0
        self._cycle_id = None
        return retry_token

    def _recovery_is_current_locked(self, cycle_id: AdbServerRecoveryCycleId) -> bool:
        return (
            not self._closed
            and bool(self._subscriptions)
            and self._recovery_enabled
            and self.server is None
            and self._cycle_id == cycle_id
        )

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("ADB server supervisor is closed")

    def _retry_delay(self, attempt_number: int) -> float:
        base = min(
            self._policy.retry_initial_seconds
            * (self._policy.retry_multiplier ** max(0, attempt_number - 1)),
            self._policy.retry_max_seconds,
        )
        jitter = self._policy.retry_jitter_ratio
        sample = self._random()
        if not 0.0 <= sample <= 1.0:
            raise ValueError("server supervision random source must return a value in [0, 1]")
        factor = 1.0 + ((sample * 2.0) - 1.0) * jitter
        return max(base * factor, 1e-6)



__all__ = ["AdbServerSupervisor"]
