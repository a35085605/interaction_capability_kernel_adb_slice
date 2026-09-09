from __future__ import annotations

from datetime import timedelta
from threading import RLock, Thread, current_thread

from networking import TcpAddress
from adb.transport_list.watch.contract import (
    AdbTransportListWatchLifecycle,
    AdbTransportListWatchReleaseApplied,
)
from adb.transport_list.watch.generation import AdbTransportListWatchGeneration
from adb.transport_list.watch.supervision.policy import (
    AdbTransportListWatchRecoveryPolicy,
)
from adb.transport_list.watch.supervision.recovery import (
    AdbTransportListWatchRecovery,
    AdbTransportListWatchRecoveryAcquired,
    AdbTransportListWatchRecoveryAttempt,
    AdbTransportListWatchRecoveryDecision,
    AdbTransportListWatchRecoveryFailed,
)
from scheduling import ScheduleToken, TemporalScheduler


class AdbTransportListWatchSupervisor:
    """Run explicitly reconciled transport-list watch recovery cycles.

    Owns generation-fenced reconciliation commands, private retry scheduling, and recovery worker
    lifetimes. Watch authority remains in the lifecycle. Retry timing is execution plumbing rather
    than an externally observable notification stream.
    """

    def __init__(
        self,
        lifecycle: AdbTransportListWatchLifecycle,
        *,
        scheduler: TemporalScheduler | None,
        policy: AdbTransportListWatchRecoveryPolicy,
        recovery_enabled: bool,
    ) -> None:
        if not isinstance(lifecycle, AdbTransportListWatchLifecycle):
            raise TypeError("lifecycle must satisfy AdbTransportListWatchLifecycle")
        if scheduler is not None and not isinstance(scheduler, TemporalScheduler):
            raise TypeError("scheduler must satisfy TemporalScheduler or be None")
        if not isinstance(policy, AdbTransportListWatchRecoveryPolicy):
            raise TypeError("policy must be AdbTransportListWatchRecoveryPolicy")
        if not isinstance(recovery_enabled, bool):
            raise TypeError("recovery_enabled must be bool")
        self._lifecycle = lifecycle
        self._scheduler = scheduler
        self._policy = policy
        self._recovery_enabled = recovery_enabled

        self._lock = RLock()
        self._recovery: AdbTransportListWatchRecovery | None = None
        self._recovery_attempt: AdbTransportListWatchRecoveryAttempt | None = None
        self._recovery_endpoint: TcpAddress | None = None
        self._retry_token: ScheduleToken | None = None
        self._attempt_threads: set[Thread] = set()
        self._pending_recovery_endpoint: TcpAddress | None = None
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
        """Start transport-list watch recovery supervision."""

        with self._lock:
            if self._closed:
                raise RuntimeError("ADB transport-list watch supervisor is closed")
            if self._started:
                raise RuntimeError("ADB transport-list watch supervisor is already started")
            self._started = True

    def close(self) -> None:
        """Stop supervision while retaining the current healthy watch."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._started = False
            retry_token, attempt_threads = self._clear_recovery_locked()

        self._cancel_retry(retry_token)
        self._join_attempt_threads(attempt_threads)

    def reconcile(self, generation: AdbTransportListWatchGeneration) -> None:
        """Release one expected generation and recover its detached watch endpoint."""

        if not isinstance(generation, AdbTransportListWatchGeneration):
            raise TypeError("generation must be AdbTransportListWatchGeneration")

        with self._lock:
            if self._closed:
                raise RuntimeError("ADB transport-list watch supervisor is closed")
            if not self._started:
                raise RuntimeError("ADB transport-list watch supervisor is not started")

        release = self._lifecycle.release(generation)
        if (
            not isinstance(release, AdbTransportListWatchReleaseApplied)
            or release.acquisition is None
        ):
            return

        self._request_recovery(release.acquisition.endpoint)

    def _request_recovery(self, endpoint: TcpAddress) -> None:
        """Start recovery for one committed failed-watch release."""

        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")

        with self._lock:
            if (
                not self._running_locked()
                or not self._recovery_enabled
                or self._scheduler is None
            ):
                return

            if self._recovery is not None:
                self._pending_recovery_endpoint = endpoint
                return

            recovery = AdbTransportListWatchRecovery(self._policy)
            self._recovery = recovery
            self._recovery_endpoint = endpoint
            self._pending_recovery_endpoint = None
            attempt = recovery.begin()

        self._apply_recovery_attempt(recovery, attempt)

    def _apply_recovery_attempt(
        self,
        recovery: AdbTransportListWatchRecovery,
        attempt: AdbTransportListWatchRecoveryAttempt,
    ) -> None:
        """Execute immediately or arrange one private watch recovery acquisition attempt."""

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
        recovery: AdbTransportListWatchRecovery,
        attempt: AdbTransportListWatchRecoveryAttempt,
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
        recovery: AdbTransportListWatchRecovery,
        attempt: AdbTransportListWatchRecoveryAttempt,
    ) -> None:
        thread = Thread(
            target=self._run_recovery_attempt,
            args=(recovery,),
            name=f"adb-transport-list-watch-recovery-{attempt.attempt_number}",
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

    def _run_recovery_attempt(self, recovery: AdbTransportListWatchRecovery) -> None:
        active_thread = current_thread()
        try:
            with self._lock:
                if not self._is_current_recovery_locked(recovery):
                    return
                endpoint = self._recovery_endpoint
                if endpoint is None:
                    raise RuntimeError(
                        "ADB transport-list watch recovery endpoint state is inconsistent"
                    )

            result = self._lifecycle.acquire(endpoint)
            decision = recovery.decide_after(result)
            self._apply_recovery_decision(recovery, decision)
        except BaseException:
            # Contract/invariant failures are not retryable lifecycle outcomes. Release this cycle
            # so later explicit reconciliations cannot become permanently pending behind a dead
            # worker, but do not automatically restart the broken cycle.
            self._abort_recovery(recovery)
            raise
        finally:
            with self._lock:
                self._attempt_threads.discard(active_thread)

    def _apply_recovery_decision(
        self,
        recovery: AdbTransportListWatchRecovery,
        decision: AdbTransportListWatchRecoveryDecision,
    ) -> None:
        """Apply one stateful watch recovery decision through supervisor-owned effects."""

        if isinstance(decision, AdbTransportListWatchRecoveryAcquired):
            self._finish_recovery(recovery)
            return
        if isinstance(decision, AdbTransportListWatchRecoveryAttempt):
            self._apply_recovery_attempt(recovery, decision)
            return
        if isinstance(decision, AdbTransportListWatchRecoveryFailed):
            self._finish_recovery(recovery)
            return
        raise TypeError("decision must be AdbTransportListWatchRecoveryDecision")

    def _abort_recovery(self, recovery: AdbTransportListWatchRecovery) -> None:
        """Terminate a broken recovery cycle and clear its scheduled work."""

        scheduler = self._scheduler
        with self._lock:
            if self._recovery is not recovery:
                return
            retry_token = self._retry_token
            self._recovery = None
            self._recovery_attempt = None
            self._recovery_endpoint = None
            self._retry_token = None
            self._pending_recovery_endpoint = None

        if retry_token is not None and scheduler is not None:
            scheduler.cancel(retry_token)

    def _finish_recovery(self, recovery: AdbTransportListWatchRecovery) -> None:
        """Release one terminal recovery cycle and consume queued recovery demand."""

        scheduler = self._scheduler
        with self._lock:
            if not self._is_current_recovery_locked(recovery):
                return
            retry_token = self._retry_token
            self._recovery = None
            self._recovery_attempt = None
            self._recovery_endpoint = None
            self._retry_token = None
            pending_endpoint = self._pending_recovery_endpoint
            self._pending_recovery_endpoint = None
            running = self._running_locked()

        if retry_token is not None and scheduler is not None:
            scheduler.cancel(retry_token)
        if pending_endpoint is not None and running:
            self._request_recovery(pending_endpoint)

    def _is_current_recovery_locked(
        self,
        recovery: AdbTransportListWatchRecovery,
    ) -> bool:
        return self._running_locked() and self._recovery is recovery

    def _running_locked(self) -> bool:
        return not self._closed and self._started

    def _clear_recovery_locked(
        self,
    ) -> tuple[ScheduleToken | None, tuple[Thread, ...]]:
        retry_token = self._retry_token
        self._recovery = None
        self._recovery_attempt = None
        self._recovery_endpoint = None
        self._retry_token = None
        self._pending_recovery_endpoint = None
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


__all__ = ["AdbTransportListWatchSupervisor"]
