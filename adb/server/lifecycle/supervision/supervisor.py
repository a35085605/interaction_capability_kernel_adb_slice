from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from random import random
from threading import Lock, Thread, current_thread

from adb.server.lifecycle.control.result import (
    AdbServerProvisionDeferred,
    AdbServerProvisionFailed,
    AdbServerProvisionResult,
    AdbServerProvisioned,
)
from adb.server.lifecycle.supervision.policy import AdbServerSupervisionPolicy
from adb.server.lifecycle.supervision.transition import (
    AdbServerRecoveryActivate,
    AdbServerRecoveryAttempt,
    AdbServerRecoveryDefer,
    AdbServerRecoveryExhaust,
    AdbServerRecoveryRetry,
    AdbServerRecoveryTransition,
    transition_recovery,
)
from adb.server.failure import (
    AdbServerConnectionFailure,
    AdbServerLivenessFailure,
    AdbServerProcessExitedFailure,
)
from adb.server.identity import AdbServer
from adb.server.state import AdbServerState, AdbServerStateView
from adb.server.signal import (
    AdbServerRecoveryCycleId,
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


class AdbServerSupervisor:
    """Reconcile ADB server failures across successive server lifetimes.

    ``AdbServerState`` holds current-lifetime truth; automatic replacement is fixed at composition.
    """

    def __init__(
        self,
        server: AdbServer | AdbServerState,
        provision_server: Callable[[], AdbServerProvisionResult],
        retire_server: Callable[[AdbServer], None],
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
        if not callable(provision_server):
            raise TypeError("provision_server must be callable")
        if not callable(retire_server):
            raise TypeError("retire_server must be callable")
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
        self._provision_server_callback = provision_server
        self._retire_server_callback = retire_server
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
        """Whether retired servers may be replaced automatically."""

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
            self._launch_recovery_attempt(launch_cycle, AdbServerRecoveryAttempt(1))

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

        # Domain retirement is authoritative immediately.  Backend attachment disposal is
        # independent and may race successor provisioning.
        try:
            self._bus.publish(AdbServerRetired(retired_server))
            self._bus.publish(AdbServerLost(retired_server, failure))
        finally:
            self._launch_retired_disposal(retired_server)
            if launch_cycle is not None:
                self._launch_recovery_attempt(launch_cycle, AdbServerRecoveryAttempt(1))

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
        self._retire_server_callback(server)

    def _launch_recovery_attempt(
        self,
        cycle_id: AdbServerRecoveryCycleId,
        attempt: AdbServerRecoveryAttempt,
    ) -> None:
        thread = self._thread_factory(
            target=self._run_recovery_attempt,
            args=(cycle_id, attempt),
            name=(
                "adb-server-recovery-"
                f"{cycle_id.value[:12]}-{attempt.attempt_number}"
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

    def _provision_server(self) -> AdbServerProvisionResult:
        result = self._provision_server_callback()
        if not isinstance(
            result,
            (AdbServerProvisioned, AdbServerProvisionDeferred, AdbServerProvisionFailed),
        ):
            raise TypeError("provision_server callback must return AdbServerProvisionResult")
        return result

    def _run_recovery_attempt(
        self,
        cycle_id: AdbServerRecoveryCycleId,
        attempt: AdbServerRecoveryAttempt,
    ) -> None:
        active = current_thread()
        transition: AdbServerRecoveryTransition | None = None
        recovered_event: AdbServerRecovered | None = None
        retry_token: ScheduleToken | None = None
        try:
            with self._mutation_lock:
                with self._lock:
                    if not self._recovery_is_current_locked(cycle_id):
                        return

                transition = transition_recovery(
                    attempt,
                    self._provision_server(),
                    max_attempts=self._policy.max_attempts,
                )

                if isinstance(transition, AdbServerRecoveryActivate):
                    with self._lock:
                        if not self._recovery_is_current_locked(cycle_id):
                            return
                        if not self._server_state.activate(transition.server):
                            return
                        retry_token = self._retry_token
                        self._retry_token = None
                        self._cycle_id = None
                        recovered_event = AdbServerRecovered(transition.server)

            if recovered_event is not None:
                if retry_token is not None:
                    self._scheduler.cancel(retry_token)
                self._bus.publish(recovered_event)
                return

            if isinstance(transition, AdbServerRecoveryDefer):
                self._schedule_retry(
                    cycle_id,
                    transition.next_attempt,
                    self._policy.deferred_retry_seconds,
                )
                return

            if isinstance(transition, AdbServerRecoveryRetry):
                self._schedule_retry(
                    cycle_id,
                    transition.next_attempt,
                    self._retry_delay(transition.next_attempt.launch_attempts),
                )
                return

            if isinstance(transition, AdbServerRecoveryExhaust):
                self._end_recovery_cycle(cycle_id)
                self._bus.publish(
                    AdbServerRecoveryExhausted(
                        cycle_id,
                        transition.attempts,
                        transition.failure,
                    )
                )
        finally:
            with self._lock:
                self._attempt_threads.discard(active)

    def _schedule_retry(
        self,
        cycle_id: AdbServerRecoveryCycleId,
        next_attempt: AdbServerRecoveryAttempt,
        delay_seconds: float,
    ) -> None:
        retry_event = AdbServerRecoveryRetryDue(
            cycle_id,
            next_attempt.attempt_number,
            next_attempt.launch_attempts,
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
        self._launch_recovery_attempt(
            event.cycle_id,
            AdbServerRecoveryAttempt(
                event.attempt_number,
                event.launch_attempts,
            ),
        )

    def _new_recovery_cycle_locked(self) -> AdbServerRecoveryCycleId:
        if self._cycle_id is not None:
            return self._cycle_id
        cycle_id = AdbServerRecoveryCycleId.new()
        self._cycle_id = cycle_id
        return cycle_id

    def _end_recovery_cycle(self, cycle_id: AdbServerRecoveryCycleId) -> None:
        with self._lock:
            if self._cycle_id != cycle_id:
                return
            retry_token = self._retry_token
            self._retry_token = None
            self._cycle_id = None
        if retry_token is not None:
            self._scheduler.cancel(retry_token)

    def _invalidate_recovery_locked(self) -> ScheduleToken | None:
        retry_token = self._retry_token
        self._retry_token = None
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
