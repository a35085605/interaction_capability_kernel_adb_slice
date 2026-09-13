from __future__ import annotations

from dataclasses import dataclass
from threading import Event, RLock, Thread, current_thread

from adb.server.generation import AdbServerGeneration
from adb.server.lifecycle import (
    AdbServerAcquireFailed,
    AdbServerAcquireReleaseRequired,
    AdbServerGenerationMismatch,
    AdbServerLifecycle,
    AdbServerReleaseAlreadyIdle,
    AdbServerReleaseRequestMismatch,
    AdbServerReleaseSucceeded,
)
from adb.server.request import AdbServerRequest
from adb.server.supervision.policy import (
    AdbServerRecoveryPolicy,
    AdbServerReleaseSupervisionPolicy,
)
from adb.server.supervision.recovery import (
    AdbServerRecovery,
    RecoveryAcquired,
    RecoveryAttempt,
    RecoveryFailed,
)
from adb.server.supervision.release import AdbServerReleaseSupervisor


@dataclass(frozen=True, slots=True)
class _RecoveryTarget:
    generation: AdbServerGeneration
    request: AdbServerRequest

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")
        if not isinstance(self.request, AdbServerRequest):
            raise TypeError("request must be AdbServerRequest")


class AdbServerSupervisor:
    """Run explicitly reconciled ADB server recovery cycles.

    Reconciliation releases one exact ``(generation, request)`` target. A successful
    release provides the next generation directly, which is carried through recovery
    attempts so reacquisition never needs a separate state read.

    The simplified lifecycle retains failed acquisition resources in
    ``RELEASE_REQUIRED``. Recovery therefore completes that exact release before retrying
    the request in the next generation. Generation mismatches resynchronize the recovery
    target from the returned current generation.
    """

    def __init__(
        self,
        lifecycle: AdbServerLifecycle,
        *,
        policy: AdbServerRecoveryPolicy,
        recovery_enabled: bool,
    ) -> None:
        if not isinstance(lifecycle, AdbServerLifecycle):
            raise TypeError("lifecycle must satisfy AdbServerLifecycle")
        if not isinstance(policy, AdbServerRecoveryPolicy):
            raise TypeError("policy must be AdbServerRecoveryPolicy")
        if not isinstance(recovery_enabled, bool):
            raise TypeError("recovery_enabled must be bool")
        self._lifecycle = lifecycle
        self._policy = policy
        self._recovery_enabled = recovery_enabled
        self._release_supervisor = AdbServerReleaseSupervisor(
            lifecycle,
            policy=AdbServerReleaseSupervisionPolicy(
                retry_seconds=policy.deferred_retry_seconds,
            ),
        )

        self._lock = RLock()
        self._stop_event = Event()
        self._recovery: AdbServerRecovery | None = None
        self._recovery_target: _RecoveryTarget | None = None
        self._recovery_threads: set[Thread] = set()
        self._pending_recovery_target: _RecoveryTarget | None = None
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
        """Stop supervision without releasing the current lifecycle request."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._started = False
            recovery_threads = self._clear_recovery_locked()

        self._stop_event.set()
        self._join_recovery_threads(recovery_threads)

    def reconcile(
        self,
        generation: AdbServerGeneration,
        request: AdbServerRequest,
    ) -> None:
        """Release one exact server request and recover only after successful release."""

        if not isinstance(generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")
        if not isinstance(request, AdbServerRequest):
            raise TypeError("request must be AdbServerRequest")

        with self._lock:
            if self._closed:
                raise RuntimeError("ADB server supervisor is closed")
            if not self._started:
                raise RuntimeError("ADB server supervisor is not started")

        release = self._release_supervisor.supervise(generation, request)
        if not isinstance(release, AdbServerReleaseSucceeded):
            return

        self._request_recovery(_RecoveryTarget(release.next_generation, request))

    def _request_recovery(self, target: _RecoveryTarget) -> None:
        """Start recovery for one completed server release."""

        if not isinstance(target, _RecoveryTarget):
            raise TypeError("target must be _RecoveryTarget")

        with self._lock:
            if not self._running_locked() or not self._recovery_enabled:
                return

            if self._recovery is not None:
                self._pending_recovery_target = target
                return

            recovery = AdbServerRecovery(self._policy)
            attempt = recovery.begin()
            self._recovery = recovery
            self._recovery_target = target
            self._pending_recovery_target = None

        self._launch_recovery_worker(recovery, attempt)

    def _launch_recovery_worker(
        self,
        recovery: AdbServerRecovery,
        attempt: RecoveryAttempt,
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
                    self._recovery_target = None
                    self._pending_recovery_target = None
                raise

    def _run_recovery(
        self,
        recovery: AdbServerRecovery,
        attempt: RecoveryAttempt,
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
                    target = self._recovery_target
                    if target is None:
                        raise RuntimeError("ADB server recovery target state is inconsistent")

                result = self._lifecycle.acquire(target.generation, target.request)

                if isinstance(result, AdbServerGenerationMismatch):
                    self._replace_recovery_target(
                        recovery,
                        _RecoveryTarget(result.current_generation, target.request),
                    )
                elif isinstance(
                    result,
                    (AdbServerAcquireFailed, AdbServerAcquireReleaseRequired),
                ):
                    self._release_failed_acquire(recovery, result)

                decision = recovery.decide_after(result)
                if isinstance(decision, RecoveryAttempt):
                    attempt = decision
                    continue
                if isinstance(decision, (RecoveryAcquired, RecoveryFailed)):
                    self._finish_recovery(recovery)
                    return
                raise TypeError("decision must be AdbServerRecoveryDecision")
        except BaseException:
            # Contract/invariant failures are not retryable lifecycle outcomes. Release this cycle
            # so later explicit reconciliations cannot become permanently pending behind a dead
            # worker, but do not automatically restart the broken cycle.
            self._abort_recovery(recovery)
            raise
        finally:
            with self._lock:
                self._recovery_threads.discard(active_thread)

    def _release_failed_acquire(
        self,
        recovery: AdbServerRecovery,
        result: AdbServerAcquireFailed | AdbServerAcquireReleaseRequired,
    ) -> None:
        """Complete RELEASE_REQUIRED before another recovery acquisition is selected."""

        snapshot = result.snapshot
        if not isinstance(snapshot.generation, AdbServerGeneration):
            raise TypeError("server failed acquire generation must be AdbServerGeneration")
        if not isinstance(snapshot.request, AdbServerRequest):
            raise TypeError("server failed acquire request must be AdbServerRequest")

        release = self._release_supervisor.supervise(
            snapshot.generation,
            snapshot.request,
        )
        if isinstance(release, AdbServerReleaseSucceeded):
            next_generation = release.next_generation
        elif isinstance(release, AdbServerGenerationMismatch):
            next_generation = release.current_generation
        elif isinstance(release, AdbServerReleaseAlreadyIdle):
            next_generation = snapshot.generation
        elif isinstance(release, AdbServerReleaseRequestMismatch):
            raise RuntimeError(
                "ADB server failed-acquire cleanup targeted a different current request"
            )
        else:
            raise TypeError("unsupported server release supervision result")

        self._replace_recovery_target(
            recovery,
            _RecoveryTarget(next_generation, snapshot.request),
        )

    def _replace_recovery_target(
        self,
        recovery: AdbServerRecovery,
        target: _RecoveryTarget,
    ) -> None:
        with self._lock:
            if self._is_current_recovery_locked(recovery):
                self._recovery_target = target

    def _abort_recovery(self, recovery: AdbServerRecovery) -> None:
        """Terminate a broken recovery cycle and clear its queued work."""

        with self._lock:
            if self._recovery is not recovery:
                return
            self._recovery = None
            self._recovery_target = None
            self._pending_recovery_target = None

    def _finish_recovery(self, recovery: AdbServerRecovery) -> None:
        """Release one terminal recovery cycle and consume queued recovery demand."""

        with self._lock:
            if not self._is_current_recovery_locked(recovery):
                return
            self._recovery = None
            self._recovery_target = None
            pending_target = self._pending_recovery_target
            self._pending_recovery_target = None
            running = self._running_locked()

        if pending_target is not None and running:
            self._request_recovery(pending_target)

    def _is_current_recovery_locked(self, recovery: AdbServerRecovery) -> bool:
        return self._running_locked() and self._recovery is recovery

    def _running_locked(self) -> bool:
        return not self._closed and self._started

    def _clear_recovery_locked(self) -> tuple[Thread, ...]:
        self._recovery = None
        self._recovery_target = None
        self._pending_recovery_target = None
        return tuple(self._recovery_threads)

    @staticmethod
    def _join_recovery_threads(threads: tuple[Thread, ...]) -> None:
        for thread in threads:
            if thread is not current_thread():
                thread.join()


__all__ = ["AdbServerSupervisor"]
