from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from random import random
from threading import Lock, Thread, current_thread

from adb.server.lifecycle.supervision.intent import (
    AdbServerEnsureIntent,
    AdbServerLifecycleIntentDispatcher,
)
from adb.server.lifecycle.supervision.policy import AdbServerRecoveryPolicy
from adb.server.lifecycle.supervision.transition import (
    AdbServerRecoveryAttempt,
    AdbServerRecoveryDefer,
    AdbServerRecoveryExhaust,
    AdbServerRecoveryRetry,
    AdbServerRecoverySucceeded,
    AdbServerRecoveryTransition,
    transition_recovery,
)
from adb.server.signal import (
    AdbServerRecoveryCycleId,
    AdbServerRecoveryExhausted,
    AdbServerRecoveryRetryDue,
)
from eventing import EventBus, EventSubscriptionToken
from scheduling import ScheduleToken, TemporalScheduler


_RandomSource = Callable[[], float]
_ThreadFactory = Callable[..., Thread]


def _default_thread_factory(*args: object, **kwargs: object) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


class AdbServerRecoveryCycle:
    """Run one bounded inactive-to-active ADB server recovery cycle.

    The runtime owns failure reconciliation and decides when a recovery cycle is needed. This
    object only drives ensure-intent results through retry policy until the ensure is satisfied,
    the launch-attempt budget is exhausted, or the cycle is closed.
    """

    def __init__(
        self,
        lifecycle: AdbServerLifecycleIntentDispatcher,
        event_bus: EventBus,
        scheduler: TemporalScheduler[object],
        policy: AdbServerRecoveryPolicy,
        *,
        _random: _RandomSource = random,
        _thread_factory: _ThreadFactory = _default_thread_factory,
        _on_finished: Callable[[AdbServerRecoveryCycle], None] | None = None,
    ) -> None:
        if not isinstance(lifecycle, AdbServerLifecycleIntentDispatcher):
            raise TypeError(
                "lifecycle must satisfy AdbServerLifecycleIntentDispatcher"
            )
        if not callable(getattr(event_bus, "publish", None)) or not callable(
            getattr(event_bus, "subscribe", None)
        ) or not callable(getattr(event_bus, "unsubscribe", None)):
            raise TypeError("event_bus must satisfy EventBus")
        if not isinstance(scheduler, TemporalScheduler):
            raise TypeError("scheduler must satisfy TemporalScheduler")
        if not isinstance(policy, AdbServerRecoveryPolicy):
            raise TypeError("policy must be AdbServerRecoveryPolicy")
        if _on_finished is not None and not callable(_on_finished):
            raise TypeError("_on_finished must be callable or None")

        self._lifecycle = lifecycle
        self._bus = event_bus
        self._scheduler = scheduler
        self._policy = policy
        self._random = _random
        self._thread_factory = _thread_factory
        self._on_finished = _on_finished
        self._cycle_id = AdbServerRecoveryCycleId.new()

        self._lock = Lock()
        self._mutation_lock = Lock()
        self._retry_subscription: EventSubscriptionToken | None = None
        self._retry_token: ScheduleToken | None = None
        self._attempt_threads: set[Thread] = set()
        self._succeeded: AdbServerRecoverySucceeded | None = None
        self._started = False
        self._finished = False
        self._closed = False

    @property
    def cycle_id(self) -> AdbServerRecoveryCycleId:
        return self._cycle_id

    @property
    def active(self) -> bool:
        with self._lock:
            return self._started and not self._finished and not self._closed

    @property
    def finished(self) -> bool:
        with self._lock:
            return self._finished

    @property
    def succeeded(self) -> AdbServerRecoverySucceeded | None:
        """Return the successful terminal result, or ``None`` when not successfully finished."""

        with self._lock:
            return self._succeeded

    def start(self) -> None:
        """Start this single-use recovery cycle with the first ensure attempt."""

        with self._mutation_lock:
            with self._lock:
                if self._closed:
                    raise RuntimeError("ADB server recovery cycle is closed")
                if self._started:
                    raise RuntimeError("ADB server recovery cycle is already started")

            subscription = self._bus.subscribe(
                AdbServerRecoveryRetryDue,
                self._on_retry_due,
            )
            with self._lock:
                if self._closed:
                    self._bus.unsubscribe(subscription)
                    raise RuntimeError("ADB server recovery cycle is closed")
                self._retry_subscription = subscription
                self._started = True

        try:
            self._launch_recovery_attempt(AdbServerRecoveryAttempt(1))
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Cancel pending retry work and wait for any in-flight ensure attempt to finish."""

        with self._mutation_lock:
            with self._lock:
                if self._closed:
                    return
                self._closed = True
                subscription = self._retry_subscription
                self._retry_subscription = None
                retry_token = self._retry_token
                self._retry_token = None
                attempt_threads = tuple(self._attempt_threads)

        if subscription is not None:
            self._bus.unsubscribe(subscription)
        if retry_token is not None:
            self._scheduler.cancel(retry_token)
        for thread in attempt_threads:
            if thread is not current_thread():
                thread.join()

    def _launch_recovery_attempt(self, attempt: AdbServerRecoveryAttempt) -> None:
        thread = self._thread_factory(
            target=self._run_recovery_attempt,
            args=(attempt,),
            name=(
                "adb-server-recovery-"
                f"{self._cycle_id.value[:12]}-{attempt.attempt_number}"
            ),
        )
        with self._lock:
            if not self._is_active_locked():
                return
            self._attempt_threads.add(thread)
            try:
                thread.start()
            except BaseException:
                self._attempt_threads.discard(thread)
                raise

    def _run_recovery_attempt(self, attempt: AdbServerRecoveryAttempt) -> None:
        active_thread = current_thread()
        transition: AdbServerRecoveryTransition | None = None
        try:
            with self._mutation_lock:
                with self._lock:
                    if not self._is_active_locked():
                        return

                ensure_result = self._lifecycle.dispatch(AdbServerEnsureIntent())
                transition = transition_recovery(
                    attempt,
                    ensure_result,
                    max_attempts=self._policy.max_attempts,
                )

            if isinstance(transition, AdbServerRecoverySucceeded):
                self._finish(succeeded=transition)
                return

            if isinstance(transition, AdbServerRecoveryDefer):
                self._schedule_retry(
                    transition.next_attempt,
                    self._policy.deferred_retry_seconds,
                )
                return

            if isinstance(transition, AdbServerRecoveryRetry):
                self._schedule_retry(
                    transition.next_attempt,
                    self._retry_delay(transition.next_attempt.launch_attempts),
                )
                return

            if isinstance(transition, AdbServerRecoveryExhaust):
                self._finish(
                    exhausted=AdbServerRecoveryExhausted(
                        self._cycle_id,
                        transition.attempts,
                        transition.failure,
                    )
                )
        finally:
            with self._lock:
                self._attempt_threads.discard(active_thread)

    def _schedule_retry(
        self,
        next_attempt: AdbServerRecoveryAttempt,
        delay_seconds: float,
    ) -> None:
        retry_event = AdbServerRecoveryRetryDue(
            self._cycle_id,
            next_attempt.attempt_number,
            next_attempt.launch_attempts,
        )
        token = self._scheduler.schedule_after(
            timedelta(seconds=delay_seconds),
            retry_event,
        )
        with self._lock:
            if not self._is_active_locked():
                self._scheduler.cancel(token)
                return
            old_token = self._retry_token
            self._retry_token = token
        if old_token is not None:
            self._scheduler.cancel(old_token)

    def _on_retry_due(self, event: AdbServerRecoveryRetryDue) -> None:
        with self._lock:
            if event.cycle_id != self._cycle_id or not self._is_active_locked():
                return
            self._retry_token = None
        self._launch_recovery_attempt(
            AdbServerRecoveryAttempt(
                event.attempt_number,
                event.launch_attempts,
            )
        )

    def _finish(
        self,
        *,
        succeeded: AdbServerRecoverySucceeded | None = None,
        exhausted: AdbServerRecoveryExhausted | None = None,
    ) -> None:
        if succeeded is not None and exhausted is not None:
            raise ValueError("recovery cycle cannot succeed and exhaust simultaneously")
        if succeeded is None and exhausted is None:
            raise ValueError("recovery cycle requires one terminal result")

        with self._mutation_lock:
            with self._lock:
                if not self._is_active_locked():
                    return
                self._succeeded = succeeded
                self._finished = True
                subscription = self._retry_subscription
                self._retry_subscription = None
                retry_token = self._retry_token
                self._retry_token = None
                on_finished = self._on_finished

        if subscription is not None:
            self._bus.unsubscribe(subscription)
        if retry_token is not None:
            self._scheduler.cancel(retry_token)

        try:
            if exhausted is not None:
                self._bus.publish(exhausted)
        finally:
            if on_finished is not None:
                on_finished(self)

    def _is_active_locked(self) -> bool:
        return self._started and not self._finished and not self._closed

    def _retry_delay(self, attempt_number: int) -> float:
        base = min(
            self._policy.retry_initial_seconds
            * (self._policy.retry_multiplier ** max(0, attempt_number - 1)),
            self._policy.retry_max_seconds,
        )
        jitter = self._policy.retry_jitter_ratio
        sample = self._random()
        if not 0.0 <= sample <= 1.0:
            raise ValueError("server recovery random source must return a value in [0, 1]")
        factor = 1.0 + ((sample * 2.0) - 1.0) * jitter
        return max(base * factor, 1e-6)


__all__ = ["AdbServerRecoveryCycle"]
