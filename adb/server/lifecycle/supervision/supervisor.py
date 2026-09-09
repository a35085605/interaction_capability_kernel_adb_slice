from __future__ import annotations

from threading import Event, RLock, Thread, current_thread

from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.generation import AdbServerGeneration
from adb.server.lifecycle.contract import AdbServerLifecycle, AdbServerReleaseApplied
from adb.server.lifecycle.supervision.policy import AdbServerRecoveryPolicy
from adb.server.lifecycle.supervision.recovery import (
    AdbServerRecovery,
    AdbServerRecoveryAcquired,
    AdbServerRecoveryAttempt,
    AdbServerRecoveryFailed,
)


class AdbServerSupervisor:
    """Run explicitly reconciled ADB server recovery cycles.

    Owns generation-fenced reconciliation commands, interruptible retry waits, and recovery worker
    lifetimes. Server authority remains in the lifecycle; retry timing is execution plumbing rather
    than an externally observable notification stream.
    """

    def __init__(
        self,
        lifecycle: AdbServerLifecycle,
        *,
        policy: AdbServerRecoveryPolicy,
        recovery_enabled: bool,
        endpoint_constraint: AdbServerEndpoint | None = None,
    ) -> None:
        if not isinstance(lifecycle, AdbServerLifecycle):
            raise TypeError("lifecycle must satisfy AdbServerLifecycle")
        if not isinstance(policy, AdbServerRecoveryPolicy):
            raise TypeError("policy must be AdbServerRecoveryPolicy")
        if not isinstance(recovery_enabled, bool):
            raise TypeError("recovery_enabled must be bool")
        if endpoint_constraint is not None and not isinstance(endpoint_constraint, TcpAddress):
            raise TypeError("endpoint_constraint must be TcpAddress or None")
        self._lifecycle = lifecycle
        self._policy = policy
        self._recovery_enabled = recovery_enabled
        self._endpoint_constraint = endpoint_constraint

        self._lock = RLock()
        self._stop_event = Event()
        self._recovery: AdbServerRecovery | None = None
        self._recovery_threads: set[Thread] = set()
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
            recovery_threads = self._clear_recovery_locked()

        self._stop_event.set()
        self._join_recovery_threads(recovery_threads)

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
            if not self._running_locked() or not self._recovery_enabled:
                return

            if self._recovery is not None:
                self._reconciliation_pending = True
                return

            recovery = AdbServerRecovery(self._policy)
            attempt = recovery.begin()
            self._recovery = recovery
            self._reconciliation_pending = False

        self._launch_recovery_worker(recovery, attempt)

    def _launch_recovery_worker(
        self,
        recovery: AdbServerRecovery,
        attempt: AdbServerRecoveryAttempt,
    ) -> None:
        thread = Thread(
            target=self._run_recovery,
            args=(recovery, attempt),
            name="adb-server-recovery",
            daemon=True,
        )
        with self._lock:
            if not self._is_current_recovery_locked(recovery):
                return
            self._recovery_threads.add(thread)
            try:
                thread.start()
            except BaseException:
                self._recovery_threads.discard(thread)
                if self._recovery is recovery:
                    self._recovery = None
                    self._reconciliation_pending = False
                raise

    def _run_recovery(
        self,
        recovery: AdbServerRecovery,
        attempt: AdbServerRecoveryAttempt,
    ) -> None:
        active_thread = current_thread()
        try:
            while True:
                if (
                    attempt.delay_seconds > 0.0
                    and self._stop_event.wait(attempt.delay_seconds)
                ):
                    return

                with self._lock:
                    if not self._is_current_recovery_locked(recovery):
                        return

                result = self._lifecycle.acquire(self._endpoint_constraint)
                decision = recovery.decide_after(result)

                if isinstance(decision, AdbServerRecoveryAttempt):
                    attempt = decision
                    continue
                if isinstance(decision, (AdbServerRecoveryAcquired, AdbServerRecoveryFailed)):
                    self._finish_recovery(recovery)
                    return
                raise TypeError("decision must be AdbServerRecoveryDecision")
        except BaseException:
            # Contract/invariant failures are not retryable lifecycle outcomes. Release this cycle so
            # later explicit reconciliations cannot become permanently pending behind a dead
            # worker, but do not automatically restart the broken cycle.
            self._abort_recovery(recovery)
            raise
        finally:
            with self._lock:
                self._recovery_threads.discard(active_thread)

    def _abort_recovery(self, recovery: AdbServerRecovery) -> None:
        """Terminate a broken recovery cycle and clear its queued work."""

        with self._lock:
            if self._recovery is not recovery:
                return
            self._recovery = None
            self._reconciliation_pending = False

    def _finish_recovery(self, recovery: AdbServerRecovery) -> None:
        """Release one terminal recovery cycle and consume queued reconciliation demand."""

        with self._lock:
            if not self._is_current_recovery_locked(recovery):
                return
            self._recovery = None
            pending_reconciliation = self._reconciliation_pending
            self._reconciliation_pending = False
            running = self._running_locked()

        if pending_reconciliation and running:
            self._request_recovery()

    def _is_current_recovery_locked(self, recovery: AdbServerRecovery) -> bool:
        return self._running_locked() and self._recovery is recovery

    def _running_locked(self) -> bool:
        return not self._closed and self._started

    def _clear_recovery_locked(self) -> tuple[Thread, ...]:
        self._recovery = None
        self._reconciliation_pending = False
        return tuple(self._recovery_threads)

    @staticmethod
    def _join_recovery_threads(threads: tuple[Thread, ...]) -> None:
        for thread in threads:
            if thread is not current_thread():
                thread.join()


__all__ = ["AdbServerSupervisor"]
