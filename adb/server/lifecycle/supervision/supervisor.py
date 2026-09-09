from __future__ import annotations

from datetime import timedelta
from threading import RLock, Thread, current_thread

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.generation import AdbServerGeneration
from adb.server.lifecycle.contract import AdbServerLifecycle, AdbServerReleaseApplied
from adb.server.lifecycle.supervision.policy import AdbServerRecoveryPolicy
from adb.server.lifecycle.supervision.recovery import (
    AdbServerRecovery,
    AdbServerRecoveryAcquired,
    AdbServerRecoveryAttempt,
    AdbServerRecoveryDecision,
    AdbServerRecoveryFailed,
)
from scheduling import ScheduleToken, TemporalScheduler


class AdbServerSupervisor:
    """Run explicitly reconciled ADB server recovery cycles.

    Owns generation-fenced reconciliation commands, private retry scheduling, and recovery worker
    lifetimes. Server authority remains in the lifecycle; retry timing is execution plumbing rather
    than an externally observable notification stream.
    """

    def __init__(
        self,
        lifecycle: AdbServerLifecycle,
        *,
        scheduler: TemporalScheduler | None,
        policy: AdbServerRecoveryPolicy,
        recovery_enabled: bool,
        endpoint_constraint: AdbServerEndpoint | None = None,
    ) -> None:
        if not isinstance(lifecycle, AdbServerLifecycle):
            raise TypeError("lifecycle must satisfy AdbServerLifecycle")
        if scheduler is not None and not isinstance(scheduler, TemporalScheduler):
            raise TypeError("scheduler must satisfy TemporalScheduler or be None")
        if not isinstance(policy, AdbServerRecoveryPolicy):
            raise TypeError("policy must be AdbServerRecoveryPolicy")
        if not isinstance(recovery_enabled, bool):
            raise TypeError("recovery_enabled must be bool")
        if endpoint_constraint is not None and not isinstance(endpoint_constraint, TcpAddress):
            raise TypeError("endpoint_constraint must be TcpAddress or None")
        self._lifecycle = lifecycle
        self._scheduler = scheduler
        self._policy = policy
        self._recovery_enabled = recovery_enabled
        self._endpoint_constraint = endpoint_constraint

        self._lock = RLock()
        self._recovery: AdbServerRecovery | None = None
        self._recovery_attempt: AdbServerRecoveryAttempt | None = None
        self._retry_token: ScheduleToken | None = None
        self._attempt_threads: set[Thread] = set()
        self._reconciliation_pending = False
        self._started = False
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
        """Start server recovery supervision."""

        with self._lock:
            if self._closed:
                raise RuntimeError("ADB server supervisor is closed")
            if self._started:
                raise RuntimeError("ADB server supervisor is already started")
            self._started = True

    def close(self) -> None:
        """Stop supervision while retaining the current healthy server."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._started = False
            retry_token, attempt_threads = self._clear_recovery_locked()

        self._cancel_retry(retry_token)
        self._join_attempt_threads(attempt_threads)

    def reconcile(self, generation: AdbServerGeneration) -> None:
        """Release one expected generation and recover when it owned a usable server."""

        if not isinstance(generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")

        with self._lock:
            if self._closed:
                raise RuntimeError("ADB server supervisor is closed")
            if not self._started:
                raise RuntimeError("ADB server supervisor is not started")

        release = self._lifecycle.release(generation)
        if (
            not isinstance(release, AdbServerReleaseApplied)
            or release.acquisition is None
        ):
            return

        self._request_recovery()

    def _request_recovery(self) -> None:
        """Start recovery for a committed reconciliation request."""

        with self._lock:
            if (
                not self._running_locked()
                or not self._recovery_enabled
                or self._scheduler is None
            ):
                return

            if self._recovery is not None:
                self._reconciliation_pending = True
                return

            recovery = AdbServerRecovery(self._policy)
            self._recovery = recovery
            self._reconciliation_pending = False
            attempt = recovery.begin()

        self._apply_recovery_attempt(recovery, attempt)

    def _apply_recovery_attempt(
        self,
        recovery: AdbServerRecovery,
        attempt: AdbServerRecoveryAttempt,
    ) -> None:
        """Execute immediately or arrange one private recovery acquisition attempt."""

        with self._lock:
            if not self._is_current_recovery_locked(recovery):
                return

        if attempt.delay_seconds > 0.0:
            scheduler = self._scheduler
            if scheduler is None:
                return

            with self._lock:
                if not self._is_current_recovery_locked(recovery):
                    return
                old_token = self._retry_token
                self._retry_token = None
                self._recovery_attempt = attempt

            if old_token is not None:
                scheduler.cancel(old_token)

            try:
                token = scheduler.schedule_after(
                    timedelta(seconds=attempt.delay_seconds),
                    lambda: self._on_recovery_retry_due(recovery, attempt),
                )
            except BaseException:
                with self._lock:
                    if (
                        self._is_current_recovery_locked(recovery)
                        and self._recovery_attempt is attempt
                    ):
                        self._recovery_attempt = None
                raise

            with self._lock:
                if (
                    self._is_current_recovery_locked(recovery)
                    and self._recovery_attempt is attempt
                ):
                    self._retry_token = token
                    return

            scheduler.cancel(token)
            return

        self._launch_recovery_attempt(recovery, attempt)

    def _on_recovery_retry_due(
        self,
        recovery: AdbServerRecovery,
        attempt: AdbServerRecoveryAttempt,
    ) -> None:
        with self._lock:
            if (
                not self._is_current_recovery_locked(recovery)
                or self._recovery_attempt is not attempt
            ):
                return
            self._retry_token = None
            self._recovery_attempt = None

        self._launch_recovery_attempt(recovery, attempt)

    def _launch_recovery_attempt(
        self,
        recovery: AdbServerRecovery,
        attempt: AdbServerRecoveryAttempt,
    ) -> None:
        thread = Thread(
            target=self._run_recovery_attempt,
            args=(recovery,),
            name=f"adb-server-recovery-{attempt.attempt_number}",
            daemon=True,
        )
        with self._lock:
            if not self._is_current_recovery_locked(recovery):
                return
            self._attempt_threads.add(thread)
            try:
                thread.start()
            except BaseException:
                self._attempt_threads.discard(thread)
                raise

    def _run_recovery_attempt(self, recovery: AdbServerRecovery) -> None:
        active_thread = current_thread()
        try:
            with self._lock:
                if not self._is_current_recovery_locked(recovery):
                    return

            result = self._lifecycle.acquire(self._endpoint_constraint)
            decision = recovery.decide_after(result)
            self._apply_recovery_decision(recovery, decision)
        except BaseException:
            # Contract/invariant failures are not retryable lifecycle outcomes. Release this cycle so
            # later explicit reconciliations cannot become permanently pending behind a dead
            # worker, but do not automatically restart the broken cycle.
            self._abort_recovery(recovery)
            raise
        finally:
            with self._lock:
                self._attempt_threads.discard(active_thread)

    def _apply_recovery_decision(
        self,
        recovery: AdbServerRecovery,
        decision: AdbServerRecoveryDecision,
    ) -> None:
        """Apply one stateful recovery decision through supervisor-owned effects."""

        if isinstance(decision, AdbServerRecoveryAcquired):
            self._finish_recovery(recovery)
            return
        if isinstance(decision, AdbServerRecoveryAttempt):
            self._apply_recovery_attempt(recovery, decision)
            return
        if isinstance(decision, AdbServerRecoveryFailed):
            self._finish_recovery(recovery)
            return
        raise TypeError("decision must be AdbServerRecoveryDecision")

    def _abort_recovery(self, recovery: AdbServerRecovery) -> None:
        """Terminate a broken recovery cycle and clear its scheduled work."""

        scheduler = self._scheduler
        with self._lock:
            if self._recovery is not recovery:
                return
            retry_token = self._retry_token
            self._recovery = None
            self._recovery_attempt = None
            self._retry_token = None
            self._reconciliation_pending = False

        if retry_token is not None and scheduler is not None:
            scheduler.cancel(retry_token)

    def _finish_recovery(self, recovery: AdbServerRecovery) -> None:
        """Release one terminal recovery cycle and consume queued reconciliation demand."""

        scheduler = self._scheduler
        with self._lock:
            if not self._is_current_recovery_locked(recovery):
                return
            retry_token = self._retry_token
            self._recovery = None
            self._recovery_attempt = None
            self._retry_token = None
            pending_reconciliation = self._reconciliation_pending
            self._reconciliation_pending = False
            running = self._running_locked()

        if retry_token is not None and scheduler is not None:
            scheduler.cancel(retry_token)
        if pending_reconciliation and running:
            self._request_recovery()

    def _is_current_recovery_locked(self, recovery: AdbServerRecovery) -> bool:
        return self._running_locked() and self._recovery is recovery

    def _running_locked(self) -> bool:
        return not self._closed and self._started

    def _clear_recovery_locked(self) -> tuple[ScheduleToken | None, tuple[Thread, ...]]:
        retry_token = self._retry_token
        self._recovery = None
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


__all__ = ["AdbServerSupervisor"]
