from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from threading import RLock, Thread, current_thread
from typing import Protocol, runtime_checkable

from adb.server.failure import AdbServerLaunchFailure
from adb.server.identity import AdbServerIdentity
from adb.server.lifecycle.backend import AdbServerBackendAcquireResult
from adb.server.lifecycle.supervision.intent import AdbServerAcquireOnceIntent
from adb.server.lifecycle.supervision.policy import AdbServerRecoveryPolicy
from adb.server.lifecycle.supervision.recovery import (
    AdbServerRecovery,
    AdbServerRecoveryCompleted,
    AdbServerRecoveryExhaust,
)
from adb.server.signal import (
    AdbServerReconciliationRequested,
    AdbServerRecoveryExhausted,
    AdbServerRecoveryId,
    AdbServerRecoveryRetryDue,
)
from adb.server.state import AdbServerDeactivated, AdbServerStateView
from eventing import EventBus, EventSubscriptionToken
from scheduling import ScheduleToken, TemporalScheduler


@runtime_checkable
class AdbServerLifecyclePort(Protocol):
    """Minimal authoritative lifecycle operations required by server supervision."""

    def acquire_once(self) -> AdbServerBackendAcquireResult | None: ...

    def retire(
        self,
        *,
        expected_server: AdbServerIdentity | None = None,
    ) -> AdbServerDeactivated | None: ...


_ReconcileDependents = Callable[[], None]


class AdbServerSupervisor:
    """Maintain the requested authoritative ADB server lifetime across failures.

    The runtime owns the lifecycle facade and injects it through :class:`AdbServerLifecyclePort`.
    This supervisor owns only continuous orchestration: reconciliation subscriptions, bounded
    recovery cycles, retry scheduling, and recovery worker lifetime.
    """

    def __init__(
        self,
        server_state: AdbServerStateView,
        *,
        lifecycle: AdbServerLifecyclePort,
        event_bus: EventBus | None,
        scheduler: TemporalScheduler[object] | None,
        policy: AdbServerRecoveryPolicy,
        recovery_enabled: bool,
        reconcile_dependents: _ReconcileDependents,
    ) -> None:
        if not isinstance(server_state, AdbServerStateView):
            raise TypeError("server_state must satisfy AdbServerStateView")
        if not isinstance(lifecycle, AdbServerLifecyclePort):
            raise TypeError("lifecycle must satisfy AdbServerLifecyclePort")
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
        if not callable(reconcile_dependents):
            raise TypeError("reconcile_dependents must be callable")

        self._server_state = server_state
        self._lifecycle = lifecycle
        self._event_bus = event_bus
        self._scheduler = scheduler
        self._policy = policy
        self._recovery_enabled = recovery_enabled
        self._reconcile_dependents = reconcile_dependents

        self._lock = RLock()
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._recovery: AdbServerRecovery | None = None
        self._recovery_id: AdbServerRecoveryId | None = None
        self._recovery_intent: AdbServerAcquireOnceIntent | None = None
        self._retry_token: ScheduleToken | None = None
        self._attempt_threads: set[Thread] = set()
        self._recovery_pending = False
        self._started = False
        self._starting = False
        self._closed = False

    @property
    def server_state(self) -> AdbServerStateView:
        return self._server_state

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

        deactivation = self._lifecycle.retire(expected_server=event.server)
        if deactivation is None:
            return

        try:
            self._reconcile_dependents()
        finally:
            self._request_recovery()

    def _request_recovery(self) -> None:
        """Start bounded acquisition recovery when authoritative state requires it."""

        with self._lock:
            if (
                not self._running_locked()
                or not self._recovery_enabled
                or self._scheduler is None
                or self._event_bus is None
                or self._server_state.snapshot().active
            ):
                return

            if self._recovery is not None:
                self._recovery_pending = True
                return

            recovery = AdbServerRecovery(self._policy)
            recovery_id = AdbServerRecoveryId.new()
            intent = recovery.start()
            self._recovery = recovery
            self._recovery_id = recovery_id
            self._recovery_pending = False

        self._apply_recovery_intent(recovery, recovery_id, intent)

    def _apply_recovery_intent(
        self,
        recovery: AdbServerRecovery,
        recovery_id: AdbServerRecoveryId,
        intent: AdbServerAcquireOnceIntent,
    ) -> None:
        """Execute immediately or arrange the delay requested by low-level recovery."""

        with self._lock:
            if not self._is_current_recovery_locked(recovery, recovery_id):
                return
            already_active = self._server_state.snapshot().active
        if already_active:
            self._finish_recovery(recovery, recovery_id, completed=True)
            return

        if intent.delay_seconds > 0.0:
            scheduler = self._scheduler
            if scheduler is None:
                return
            token = scheduler.schedule_after(
                timedelta(seconds=intent.delay_seconds),
                AdbServerRecoveryRetryDue(recovery_id, intent.attempt_number),
            )
            with self._lock:
                if not self._is_current_recovery_locked(recovery, recovery_id):
                    scheduler.cancel(token)
                    return
                old_token = self._retry_token
                self._retry_token = token
                self._recovery_intent = intent
            if old_token is not None:
                scheduler.cancel(old_token)
            return

        self._launch_recovery_attempt(recovery, recovery_id, intent)

    def _on_recovery_retry_due(self, event: AdbServerRecoveryRetryDue) -> None:
        with self._lock:
            recovery = self._recovery
            recovery_id = self._recovery_id
            intent = self._recovery_intent
            if (
                recovery is None
                or recovery_id != event.recovery_id
                or intent is None
                or intent.attempt_number != event.attempt_number
                or not self._is_current_recovery_locked(recovery, recovery_id)
            ):
                return
            self._retry_token = None
            self._recovery_intent = None

        self._launch_recovery_attempt(recovery, recovery_id, intent)

    def _launch_recovery_attempt(
        self,
        recovery: AdbServerRecovery,
        recovery_id: AdbServerRecoveryId,
        intent: AdbServerAcquireOnceIntent,
    ) -> None:
        thread = Thread(
            target=self._run_recovery_attempt,
            args=(recovery, recovery_id, intent),
            name=f"adb-server-recovery-{recovery_id.value[:12]}-{intent.attempt_number}",
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
        intent: AdbServerAcquireOnceIntent,
    ) -> None:
        active_thread = current_thread()
        try:
            with self._lock:
                if not self._is_current_recovery_locked(recovery, recovery_id):
                    return
                finish_without_attempt = self._server_state.snapshot().active

            if finish_without_attempt:
                self._finish_recovery(recovery, recovery_id, completed=True)
                return

            acquire = self._lifecycle.acquire_once()
            if acquire is None:
                self._finish_recovery(recovery, recovery_id, completed=True)
                return

            decision = recovery.accept(acquire)
            if isinstance(decision, AdbServerAcquireOnceIntent):
                self._apply_recovery_intent(recovery, recovery_id, decision)
                return
            if isinstance(decision, AdbServerRecoveryCompleted):
                self._finish_recovery(recovery, recovery_id, completed=True)
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
                self._finish_recovery(recovery, recovery_id, completed=False)
                return
            raise TypeError("recovery returned an unsupported decision")
        finally:
            with self._lock:
                self._attempt_threads.discard(active_thread)

    def _finish_recovery(
        self,
        recovery: AdbServerRecovery,
        recovery_id: AdbServerRecoveryId,
        *,
        completed: bool,
    ) -> None:
        """Release one recovery cycle and let authoritative state decide successor work."""

        restart = False
        reconcile = False
        scheduler = self._scheduler
        with self._lock:
            if not self._is_current_recovery_locked(recovery, recovery_id):
                return
            retry_token = self._retry_token
            self._recovery = None
            self._recovery_id = None
            self._recovery_intent = None
            self._retry_token = None
            pending = self._recovery_pending
            self._recovery_pending = False

            if self._running_locked():
                active = self._server_state.snapshot().active
                reconcile = completed and active
                restart = (completed and not active) or (pending and not active)

        if retry_token is not None and scheduler is not None:
            scheduler.cancel(retry_token)
        if reconcile:
            self._reconcile_dependents()
        if restart:
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
        self._recovery_intent = None
        self._retry_token = None
        self._recovery_pending = False
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


__all__ = ["AdbServerLifecyclePort", "AdbServerSupervisor"]
