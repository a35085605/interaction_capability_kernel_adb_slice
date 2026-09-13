from __future__ import annotations

from dataclasses import dataclass, field
from threading import Condition
import unittest

from _lifecycle_new.capability.snapshot import LifecyclePhase, LifecycleSnapshot
from adb.server.capability import AdbServerCapability
from adb.server.generation import AdbServerGenerationIssuer
from adb.server.lifecycle import (
    AdbServerAcquireFailed,
    AdbServerAcquireSucceeded,
    AdbServerGenerationMismatch,
    AdbServerReleaseSucceeded,
)
from adb.server.request import AdbServerRequest
from adb.server.supervision.policy import AdbServerRecoveryPolicy
from adb.server.supervision.supervisor import AdbServerSupervisor
from adb.server.template import AdbServerAcquireError
from networking import TcpAddress


@dataclass
class _ScriptedLifecycle:
    acquire_results: list[object]
    release_results: list[object]
    acquire_calls: list[tuple[object, object]] = field(default_factory=list, init=False)
    release_calls: list[tuple[object, object]] = field(default_factory=list, init=False)
    _condition: Condition = field(default_factory=Condition, init=False, repr=False)

    def read(self):
        raise AssertionError("supervisor must not reconstruct recovery state with read()")

    def acquire(self, generation, request):
        with self._condition:
            self.acquire_calls.append((generation, request))
            if not self.acquire_results:
                raise AssertionError("unexpected acquire call")
            result = self.acquire_results.pop(0)
            self._condition.notify_all()
            return result

    def release(self, generation, request):
        with self._condition:
            self.release_calls.append((generation, request))
            if not self.release_results:
                raise AssertionError("unexpected release call")
            result = self.release_results.pop(0)
            self._condition.notify_all()
            return result

    def wait_for_acquires(self, count: int) -> None:
        with self._condition:
            if not self._condition.wait_for(
                lambda: len(self.acquire_calls) >= count,
                timeout=2.0,
            ):
                raise AssertionError(f"timed out waiting for {count} acquire call(s)")

    def wait_for_releases(self, count: int) -> None:
        with self._condition:
            if not self._condition.wait_for(
                lambda: len(self.release_calls) >= count,
                timeout=2.0,
            ):
                raise AssertionError(f"timed out waiting for {count} release call(s)")


class AdbServerSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        issuer = AdbServerGenerationIssuer()
        self.generation_1 = issuer.issue()
        self.generation_2 = issuer.issue()
        self.generation_3 = issuer.issue()
        self.request = AdbServerRequest(TcpAddress("127.0.0.1", 5037))
        self.capability = AdbServerCapability(self.request.server_address)
        self.policy = AdbServerRecoveryPolicy(
            retry_initial_seconds=0.001,
            retry_max_seconds=0.001,
            retry_multiplier=1.0,
            retry_jitter_ratio=0.0,
            deferred_retry_seconds=0.001,
            max_attempts=3,
        )

    def active_snapshot(self, generation):
        return LifecycleSnapshot(
            generation,
            self.request,
            self.capability,
            phase=LifecyclePhase.ACTIVE,
        )

    def failed_snapshot(self, generation, diagnostic="launch failed"):
        return LifecycleSnapshot(
            generation,
            self.request,
            phase=LifecyclePhase.RELEASE_REQUIRED,
            last_error=AdbServerAcquireError(diagnostic),
        )

    def supervisor(self, lifecycle, *, policy=None):
        supervisor = AdbServerSupervisor(
            lifecycle,
            policy=self.policy if policy is None else policy,
            recovery_enabled=True,
        )
        supervisor.start()
        self.addCleanup(supervisor.close)
        return supervisor

    def test_successful_reconcile_recovers_same_request_in_next_generation(self) -> None:
        lifecycle = _ScriptedLifecycle(
            acquire_results=[
                AdbServerAcquireSucceeded(self.active_snapshot(self.generation_2))
            ],
            release_results=[AdbServerReleaseSucceeded(self.generation_2)],
        )
        supervisor = self.supervisor(lifecycle)

        supervisor.reconcile(self.generation_1, self.request)
        lifecycle.wait_for_acquires(1)
        supervisor.close()

        self.assertEqual(
            lifecycle.release_calls,
            [(self.generation_1, self.request)],
        )
        self.assertEqual(
            lifecycle.acquire_calls,
            [(self.generation_2, self.request)],
        )

    def test_reconcile_generation_mismatch_does_not_start_recovery(self) -> None:
        lifecycle = _ScriptedLifecycle(
            acquire_results=[],
            release_results=[AdbServerGenerationMismatch(self.generation_2)],
        )
        supervisor = self.supervisor(lifecycle)

        supervisor.reconcile(self.generation_1, self.request)
        supervisor.close()

        self.assertEqual(lifecycle.acquire_calls, [])
        self.assertEqual(
            lifecycle.release_calls,
            [(self.generation_1, self.request)],
        )

    def test_failed_recovery_acquire_is_released_before_retrying_next_generation(self) -> None:
        lifecycle = _ScriptedLifecycle(
            acquire_results=[
                AdbServerAcquireFailed(self.failed_snapshot(self.generation_2)),
                AdbServerAcquireSucceeded(self.active_snapshot(self.generation_3)),
            ],
            release_results=[
                AdbServerReleaseSucceeded(self.generation_2),
                AdbServerReleaseSucceeded(self.generation_3),
            ],
        )
        supervisor = self.supervisor(lifecycle)

        supervisor.reconcile(self.generation_1, self.request)
        lifecycle.wait_for_acquires(2)
        supervisor.close()

        self.assertEqual(
            lifecycle.release_calls,
            [
                (self.generation_1, self.request),
                (self.generation_2, self.request),
            ],
        )
        self.assertEqual(
            lifecycle.acquire_calls,
            [
                (self.generation_2, self.request),
                (self.generation_3, self.request),
            ],
        )

    def test_generation_mismatch_resynchronizes_recovery_target(self) -> None:
        lifecycle = _ScriptedLifecycle(
            acquire_results=[
                AdbServerGenerationMismatch(self.generation_3),
                AdbServerAcquireSucceeded(self.active_snapshot(self.generation_3)),
            ],
            release_results=[AdbServerReleaseSucceeded(self.generation_2)],
        )
        supervisor = self.supervisor(lifecycle)

        supervisor.reconcile(self.generation_1, self.request)
        lifecycle.wait_for_acquires(2)
        supervisor.close()

        self.assertEqual(
            lifecycle.acquire_calls,
            [
                (self.generation_2, self.request),
                (self.generation_3, self.request),
            ],
        )

    def test_exhausted_recovery_still_releases_failed_acquisition(self) -> None:
        policy = AdbServerRecoveryPolicy(
            retry_initial_seconds=0.001,
            retry_max_seconds=0.001,
            retry_multiplier=1.0,
            retry_jitter_ratio=0.0,
            deferred_retry_seconds=0.001,
            max_attempts=1,
        )
        lifecycle = _ScriptedLifecycle(
            acquire_results=[
                AdbServerAcquireFailed(self.failed_snapshot(self.generation_2))
            ],
            release_results=[
                AdbServerReleaseSucceeded(self.generation_2),
                AdbServerReleaseSucceeded(self.generation_3),
            ],
        )
        supervisor = self.supervisor(lifecycle, policy=policy)

        supervisor.reconcile(self.generation_1, self.request)
        lifecycle.wait_for_releases(2)
        supervisor.close()

        self.assertEqual(
            lifecycle.release_calls,
            [
                (self.generation_1, self.request),
                (self.generation_2, self.request),
            ],
        )
        self.assertEqual(
            lifecycle.acquire_calls,
            [(self.generation_2, self.request)],
        )


if __name__ == "__main__":
    unittest.main()
