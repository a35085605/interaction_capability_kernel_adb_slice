from __future__ import annotations

from datetime import timedelta
from threading import RLock, Thread, current_thread

from adb.server.lifecycle.coordinator import AdbServerLifecycleCoordinator
from adb.server.lifecycle.provision import classify_provision_result
from adb.server.lifecycle.supervision.policy import AdbServerRecoveryPolicy
from adb.server.lifecycle.supervision.recovery import (
    AdbServerRecovery,
    AdbServerRecoveryAttempt,
    AdbServerRecoveryFailed,
)
from adb.server.lifecycle.supervision.transition import (
    AdbServerRecoveryCompleted,
    AdbServerRecoveryInstruction,
    decide_recovery_after_provision,
)
from adb.server.signal import (
    AdbServerReconciliationRequested,
    AdbServerRecoveryId,
    AdbServerRecoveryRetryDue,
)
from adb.server.state import AdbServerDeactivated
from eventing import EventBus, EventSubscriptionToken
from scheduling import ScheduleToken, TemporalScheduler


class AdbServerSupervisor:
    """Execute reconcile-driven bounded ADB server recovery cycles.

    The runtime owns the lifecycle coordinator and injects it into server supervision. This
    supervisor owns reconciliation subscriptions, bounded recovery cycles, retry scheduling, and
    recovery worker lifetime; it does not infer successor work from authoritative server state.
    """

    def __init__(
        self,
        lifecycle: AdbServerLifecycleCoordinator,
        *,
        event_bus: EventBus | None,
        scheduler: TemporalScheduler[object] | None,
        policy: AdbServerRecoveryPolicy,
        recovery_enabled: bool,
    ) -> None:
        if not isinstance(lifecycle, AdbServerLifecycleCoordinator):
            raise TypeError("lifecycle must be AdbServerLifecycleCoordinator")
        if event_bus is not None and not _is_event_bus(event_bus):
            raise TypeError("event_bus must satisfy EventBus or be None")
        if scheduler is not None and not isinstance(scheduler, TemporalScheduler):
            raise TypeError("scheduler must satisfy TemporalScheduler or be None")
        if scheduler is not None and event_bus is None:
            raise ValueError("scheduled server supervision requires an event bus")
        if not isinstance(policy, AdbServerRecoveryPolicy):
            raise TypeError("policy must be AdbServerRecoveryPolicy")
        if not isinstance(recovery_enabled, bool):
            raise TypeError("recovery_enabled must be bool")
        self._lifecycle = lifecycle
        self._event_bus = event_bus
        self._scheduler = scheduler
        self._policy = policy
        self._recovery_enabled = recovery_enabled

        self._lock = RLock()
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._recovery: AdbServerRecovery | None = None
        self._recovery_id: AdbServerRecoveryId | None = None
        self._recovery_attempt: AdbServerRecoveryAttempt | None = None
        self._retry_token: ScheduleToken | None = None
        self._attempt_threads: set[Thread] = set()
        self._reconciliation_pending = False
        self._started = False
        self._starting = False
        self._closed = False

    @property
    def started(self) -> bool:
        with self._lock:
            return self._started

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def start(self) -> None:
        """Start server reconciliation and recovery supervision."""

        with self._lock:
            if self._closed:
                raise RuntimeError("ADB server supervisor is closed")
            if self._started or self._starting:
                raise RuntimeError("ADB server supervisor is already started")
            self._starting = True

        subscription_tokens: list[EventSubscriptionToken] = []
        try:
            event_bus = self._event_bus
            if event_bus is not None:
                subscription_tokens.append(
                    event_bus.subscribe(
                        AdbServerReconciliationRequested,
                        self._on_reconciliation_requested,
                    )
                )
                if self._scheduler is not None:
                    subscription_tokens.append(
                        event_bus.subscribe(
                            AdbServerRecoveryRetryDue,
                            self._on_recovery_retry_due,
                        )
                    )
        except BaseException:
            if self._event_bus is not None:
                for token in subscription_tokens:
                    self._event_bus.unsubscribe(token)
            with self._lock:
                retry_token, attempt_threads = self._clear_recovery_locked()
                self._starting = False
            self._cancel_retry(retry_token)
            self._join_attempt_threads(attempt_threads)
            raise

        with self._lock:
            self._subscriptions = tuple(subscription_tokens)
            self._started = True
            self._starting = False

    def close(self) -> None:
        """Stop supervision without retiring the current healthy server lifetime."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._started = False
            self._starting = False
            subscriptions = self._subscriptions
            self._subscriptions = ()
            retry_token, attempt_threads = self._clear_recovery_locked()

        event_bus = self._event_bus
        if event_bus is not None:
            for token in subscriptions:
                event_bus.unsubscribe(token)
        self._cancel_retry(retry_token)
        self._join_attempt_threads(attempt_threads)

    def _on_reconciliation_requested(
        self,
        event: AdbServerReconciliationRequested,
    ) -> None:
        """Commit one requested retirement, then start bounded recovery."""

        with self._lock:
            if not self._running_locked():
                return

        retirement = self._lifecycle.retire(expected_server=event.server)
        if not isinstance(retirement, AdbServerDeactivated):
            return

        self._request_recovery()

    def _request_recovery(self) -> None:
        """Consume one committed reconciliation demand through bounded acquisition recovery."""

        with self._lock:
            if (
                not self._running_locked()
                or not self._recovery_enabled
                or self._scheduler is None
                or self._event_bus is None
            ):
                return

            if self._recovery is not None:
                self._reconciliation_pending = True
                return

            recovery = AdbServerRecovery(self._policy)
            recovery_id = AdbServerRecoveryId.new()
            self._recovery = recovery
            self._recovery_id = recovery_id
            self._reconciliation_pending = False
            attempt = recovery.begin()

        self._apply_recovery_attempt(recovery, recovery_id, attempt)

    def _apply_recovery_attempt(
        self,
        recovery: AdbServerRecovery,
        recovery_id: AdbServerRecoveryId,
        attempt: AdbServerRecoveryAttempt,
    ) -> None:
        """Execute immediately or arrange one recovery acquisition attempt."""

        with self._lock:
            if not self._is_current_recovery_locked(recovery, recovery_id):
                return

        if attempt.delay_seconds > 0.0:
            scheduler = self._scheduler
            if scheduler is None:
                return
            token = scheduler.schedule_after(
                timedelta(seconds=attempt.delay_seconds),
                AdbServerRecoveryRetryDue(recovery_id, attempt.attempt_number),
            )
            with self._lock:
                if not self._is_current_recovery_locked(recovery, recovery_id):
                    scheduler.cancel(token)
                    return
                old_token = self._retry_token
                self._retry_token = token
                self._recovery_attempt = attempt
            if old_token is not None:
                scheduler.cancel(old_token)
            return

        self._launch_recovery_attempt(recovery, recovery_id, attempt)

    def _on_recovery_retry_due(self, event: AdbServerRecoveryRetryDue) -> None:
        with self._lock:
            recovery = self._recovery
            recovery_id = self._recovery_id
            attempt = self._recovery_attempt
            if (
                recovery is None
                or recovery_id != event.recovery_id
                or attempt is None
                or attempt.attempt_number != event.attempt_number
                or not self._is_current_recovery_locked(recovery, recovery_id)
            ):
                return
            self._retry_token = None
            self._recovery_attempt = None

        self._launch_recovery_attempt(recovery, recovery_id, attempt)

    def _launch_recovery_attempt(
        self,
        recovery: AdbServerRecovery,
        recovery_id: AdbServerRecoveryId,
        attempt: AdbServerRecoveryAttempt,
    ) -> None:
        thread = Thread(
            target=self._run_recovery_attempt,
            args=(recovery, recovery_id),
            name=f"adb-server-recovery-{recovery_id.value[:12]}-{attempt.attempt_number}",
            daemon=True,
        )
        with self._lock:
            if not self._is_current_recovery_locked(recovery, recovery_id):
                return
            self._attempt_threads.add(thread)
            try:
                thread.start()
            except BaseException:
                self._attempt_threads.discard(thread)
                raise

    def _run_recovery_attempt(
        self,
        recovery: AdbServerRecovery,
        recovery_id: AdbServerRecoveryId,
    ) -> None:
        active_thread = current_thread()
        try:
            with self._lock:
                if not self._is_current_recovery_locked(recovery, recovery_id):
                    return

            outcome = classify_provision_result(self._lifecycle.provision())
            instruction = decide_recovery_after_provision(recovery, outcome)
            self._apply_recovery_instruction(recovery, recovery_id, instruction)
        except BaseException:
            # Contract/invariant failures are not retryable backend outcomes. Release this cycle so
            # later explicit recovery requests cannot become permanently pending behind a dead
            # worker, but do not automatically restart the broken cycle.
            self._abort_recovery(recovery, recovery_id)
            raise
        finally:
            with self._lock:
                self._attempt_threads.discard(active_thread)

    def _apply_recovery_instruction(
        self,
        recovery: AdbServerRecovery,
        recovery_id: AdbServerRecoveryId,
        instruction: AdbServerRecoveryInstruction,
    ) -> None:
        """Apply one stateful recovery instruction through supervisor-owned effects."""

        if isinstance(instruction, AdbServerRecoveryCompleted):
            self._finish_recovery(recovery, recovery_id)
            return
        if isinstance(instruction, AdbServerRecoveryAttempt):
            self._apply_recovery_attempt(recovery, recovery_id, instruction)
            return
        if isinstance(instruction, AdbServerRecoveryFailed):
            self._finish_recovery(recovery, recovery_id)
            return
        raise TypeError("instruction must be AdbServerRecoveryInstruction")

    def _abort_recovery(
        self,
        recovery: AdbServerRecovery,
        recovery_id: AdbServerRecoveryId,
    ) -> None:
        """Fail-stop one broken recovery cycle without reconciliation or automatic restart."""

        scheduler = self._scheduler
        with self._lock:
            if self._recovery is not recovery or self._recovery_id != recovery_id:
                return
            retry_token = self._retry_token
            self._recovery = None
            self._recovery_id = None
            self._recovery_attempt = None
            self._retry_token = None
            self._reconciliation_pending = False

        if retry_token is not None and scheduler is not None:
            scheduler.cancel(retry_token)

    def _finish_recovery(
        self,
        recovery: AdbServerRecovery,
        recovery_id: AdbServerRecoveryId,
    ) -> None:
        """Release one terminal recovery cycle and consume queued reconciliation demand."""

        scheduler = self._scheduler
        with self._lock:
            if not self._is_current_recovery_locked(recovery, recovery_id):
                return
            retry_token = self._retry_token
            self._recovery = None
            self._recovery_id = None
            self._recovery_attempt = None
            self._retry_token = None
            pending_reconciliation = self._reconciliation_pending
            self._reconciliation_pending = False
            running = self._running_locked()

        if retry_token is not None and scheduler is not None:
            scheduler.cancel(retry_token)
        if pending_reconciliation and running:
            self._request_recovery()

    def _is_current_recovery_locked(
        self,
        recovery: AdbServerRecovery,
        recovery_id: AdbServerRecoveryId,
    ) -> bool:
        return (
            self._running_locked()
            and self._recovery is recovery
            and self._recovery_id == recovery_id
        )

    def _running_locked(self) -> bool:
        return not self._closed and (self._started or self._starting)

    def _clear_recovery_locked(self) -> tuple[ScheduleToken | None, tuple[Thread, ...]]:
        retry_token = self._retry_token
        self._recovery = None
        self._recovery_id = None
        self._recovery_attempt = None
        self._retry_token = None
        self._reconciliation_pending = False
        return retry_token, tuple(self._attempt_threads)

    def _cancel_retry(self, token: ScheduleToken | None) -> None:
        scheduler = self._scheduler
        if token is not None and scheduler is not None:
            scheduler.cancel(token)

    @staticmethod
    def _join_attempt_threads(threads: tuple[Thread, ...]) -> None:
        for thread in threads:
            if thread is not current_thread():
                thread.join()


def _is_event_bus(value: object) -> bool:
    return (
        callable(getattr(value, "publish", None))
        and callable(getattr(value, "subscribe", None))
        and callable(getattr(value, "unsubscribe", None))
    )


__all__ = ["AdbServerSupervisor"]
