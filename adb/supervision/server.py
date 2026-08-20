from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from random import random
from threading import Lock, Thread, current_thread

from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle import (
    AdbServerAvailability,
    AdbServerEnsureAvailability,
    AdbServerEnsureOrchestrator,
    AdbServerEnsureResult,
    AdbServerEnsureStatus,
)
from adb.supervision.model import AdbServerRecoveryCycleId, AdbServerSupervisionPolicy
from adb.supervision.signal import (
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
    """Maintain the desired running condition of one configured ADB server endpoint.

    The bounded ``AdbServerEnsureOrchestrator`` remains the owner of one probe/command/
    verification episode. This supervisor owns durable running intent, the recovery-enabled
    gate, retry/backoff state, stale-cycle fencing, and serialization of managed start/stop
    mutations for the endpoint.

    Availability monitoring is intentionally not hidden here: callers may invoke ``reconcile``
    or publish ``AdbServerReconciliationRequested`` when fresh evidence or another supervised
    condition suggests the running condition should be checked again. Downstream liveness sources
    do not synthesize server availability; every accepted request still enters a fresh bounded
    server ensure episode.
    """

    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        event_bus: EventBus,
        ensure_orchestrator: AdbServerEnsureOrchestrator,
        scheduler: TemporalScheduler[object],
        policy: AdbServerSupervisionPolicy,
        *,
        _random: _RandomSource = random,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if not callable(getattr(event_bus, "publish", None)) or not callable(
            getattr(event_bus, "subscribe", None)
        ):
            raise TypeError("event_bus must satisfy EventBus")
        if not isinstance(ensure_orchestrator, AdbServerEnsureOrchestrator):
            raise TypeError("ensure_orchestrator must be AdbServerEnsureOrchestrator")
        if ensure_orchestrator.endpoint != endpoint:
            raise ValueError("ensure orchestrator endpoint does not match ADB server endpoint")
        if not isinstance(scheduler, TemporalScheduler):
            raise TypeError("scheduler must satisfy TemporalScheduler")
        if not isinstance(policy, AdbServerSupervisionPolicy):
            raise TypeError("policy must be AdbServerSupervisionPolicy")

        self.endpoint = endpoint
        self._bus = event_bus
        self._ensure = ensure_orchestrator
        self._scheduler = scheduler
        self._policy = policy
        self._random = _random
        self._thread_factory = _thread_factory

        self._lock = Lock()
        self._mutation_lock = Lock()
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._desired_running = False
        self._recovery_enabled = False
        self._recovery_epoch = 0
        self._cycle_id: AdbServerRecoveryCycleId | None = None
        self._retry_token: ScheduleToken | None = None
        self._attempt_threads: set[Thread] = set()
        self._closed = False

    @property
    def desired_running(self) -> bool:
        with self._lock:
            return self._desired_running

    @property
    def recovery_enabled(self) -> bool:
        with self._lock:
            return self._recovery_enabled

    @property
    def recovery_epoch(self) -> int:
        with self._lock:
            return self._recovery_epoch

    def start(self, *, recovery_enabled: bool) -> AdbServerEnsureResult:
        """Establish the running condition and optionally keep recovery armed afterwards."""

        enabled = _require_bool(recovery_enabled, field_name="recovery_enabled")
        with self._mutation_lock:
            with self._lock:
                self._require_open()
                old_token = self._invalidate_recovery_locked()
                self._desired_running = True
                self._recovery_enabled = enabled
                self._recovery_epoch += 1
                cycle_id = AdbServerRecoveryCycleId.new() if enabled else None
                self._cycle_id = cycle_id
                if cycle_id is not None:
                    self._ensure_recovery_subscriptions_locked()
            if old_token is not None:
                self._scheduler.cancel(old_token)

            result = self._ensure.ensure(
                AdbServerEnsureAvailability(
                    endpoint=self.endpoint,
                    desired=AdbServerAvailability.AVAILABLE,
                    policy=self._policy.ensure_policy,
                )
            )

        if cycle_id is not None:
            self._handle_recovery_result(cycle_id, 1, result)
        return result

    def stop(self) -> AdbServerEnsureResult:
        """Establish the stopped condition and invalidate automatic running recovery."""

        with self._mutation_lock:
            with self._lock:
                self._require_open()
                old_token = self._invalidate_recovery_locked()
                self._desired_running = False
                self._recovery_enabled = False
                self._recovery_epoch += 1
            if old_token is not None:
                self._scheduler.cancel(old_token)
            return self._ensure.ensure(
                AdbServerEnsureAvailability(
                    endpoint=self.endpoint,
                    desired=AdbServerAvailability.UNAVAILABLE,
                    policy=self._policy.ensure_policy,
                )
            )

    def set_recovery_enabled(self, enabled: bool) -> None:
        """Enable or disable maintenance of the server running condition without stopping it."""

        normalized = _require_bool(enabled, field_name="enabled")
        launch_cycle: AdbServerRecoveryCycleId | None = None
        with self._lock:
            self._require_open()
            if normalized and not self._desired_running:
                raise RuntimeError(
                    "cannot enable ADB server recovery without a desired running condition"
                )
            if self._recovery_enabled is normalized:
                return
            old_token = self._invalidate_recovery_locked()
            self._recovery_enabled = normalized
            self._recovery_epoch += 1
            if normalized:
                launch_cycle = AdbServerRecoveryCycleId.new()
                self._cycle_id = launch_cycle
                self._ensure_recovery_subscriptions_locked()
        if old_token is not None:
            self._scheduler.cancel(old_token)
        if launch_cycle is not None:
            self._launch_recovery_attempt(launch_cycle, attempt_number=1)

    def reconcile(self) -> None:
        """Freshly reconcile the running condition when automatic recovery is currently allowed."""

        with self._lock:
            self._require_open()
            if not self._recovery_armed_locked():
                return
            if self._cycle_id is not None:
                return
            cycle_id = AdbServerRecoveryCycleId.new()
            self._cycle_id = cycle_id
            self._recovery_epoch += 1
            self._ensure_recovery_subscriptions_locked()
        self._launch_recovery_attempt(cycle_id, attempt_number=1)

    def close(self) -> None:
        """Stop supervising without changing the native server state."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._recovery_enabled = False
            subscriptions = self._subscriptions
            self._subscriptions = ()
            retry_token = self._invalidate_recovery_locked()
            attempt_threads = tuple(self._attempt_threads)
            self._recovery_epoch += 1
        for token in subscriptions:
            self._bus.unsubscribe(token)
        if retry_token is not None:
            self._scheduler.cancel(retry_token)
        for thread in attempt_threads:
            if thread is not current_thread():
                thread.join()

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
                f"{self.endpoint.host}-{self.endpoint.port}-{attempt_number}"
            ),
        )
        with self._lock:
            if not self._recovery_is_current_locked(cycle_id):
                return
            self._attempt_threads.add(thread)
        thread.start()

    def _run_recovery_attempt(
        self,
        cycle_id: AdbServerRecoveryCycleId,
        attempt_number: int,
    ) -> None:
        active = current_thread()
        try:
            with self._mutation_lock:
                with self._lock:
                    if not self._recovery_is_current_locked(cycle_id):
                        return
                result = self._ensure.ensure(
                    AdbServerEnsureAvailability(
                        endpoint=self.endpoint,
                        desired=AdbServerAvailability.AVAILABLE,
                        policy=self._policy.ensure_policy,
                    )
                )
            self._handle_recovery_result(cycle_id, attempt_number, result)
        finally:
            with self._lock:
                self._attempt_threads.discard(active)

    def _handle_recovery_result(
        self,
        cycle_id: AdbServerRecoveryCycleId,
        attempt_number: int,
        result: AdbServerEnsureResult,
    ) -> None:
        if result.status is AdbServerEnsureStatus.SATISFIED:
            self._end_recovery_cycle(cycle_id)
            return

        with self._lock:
            if not self._recovery_is_current_locked(cycle_id):
                return
        self._schedule_retry_or_exhaust(cycle_id, attempt_number)

    def _schedule_retry_or_exhaust(
        self,
        cycle_id: AdbServerRecoveryCycleId,
        attempt_number: int,
    ) -> None:
        max_attempts = self._policy.max_attempts
        if max_attempts is not None and attempt_number >= max_attempts:
            self._end_recovery_cycle(cycle_id)
            self._bus.publish(
                AdbServerRecoveryExhausted(
                    self.endpoint,
                    cycle_id,
                    attempt_number,
                )
            )
            return

        next_attempt = attempt_number + 1
        delay_seconds = self._retry_delay(attempt_number)
        retry_event = AdbServerRecoveryRetryDue(
            self.endpoint,
            cycle_id,
            next_attempt,
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

    def _ensure_recovery_subscriptions_locked(self) -> None:
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
        if event.endpoint != self.endpoint:
            return
        with self._lock:
            if self._closed or not self._recovery_armed_locked():
                return
            if self._cycle_id is not None:
                return
            cycle_id = AdbServerRecoveryCycleId.new()
            self._cycle_id = cycle_id
            self._recovery_epoch += 1
            self._ensure_recovery_subscriptions_locked()
        self._launch_recovery_attempt(cycle_id, attempt_number=1)

    def _on_retry_due(self, event: AdbServerRecoveryRetryDue) -> None:
        if event.endpoint != self.endpoint:
            return
        with self._lock:
            if not self._recovery_is_current_locked(event.cycle_id):
                return
            self._retry_token = None
        self._launch_recovery_attempt(event.cycle_id, event.attempt_number)

    def _end_recovery_cycle(
        self,
        cycle_id: AdbServerRecoveryCycleId,
    ) -> None:
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
            and self._recovery_armed_locked()
            and self._cycle_id == cycle_id
        )

    def _recovery_armed_locked(self) -> bool:
        return self._desired_running and self._recovery_enabled

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
