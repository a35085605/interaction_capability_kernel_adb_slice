from __future__ import annotations

import unittest

from _lifecycle_new.capability.result import (
    AcquireFailed,
    AcquireSucceeded,
    LifecycleBusy,
)
from _lifecycle_new.capability.snapshot import LifecyclePhase, LifecycleSnapshot
from adb._recovery import RecoveryAcquired, RecoveryAttempt, RecoveryFailed
from adb.transport_list.model import AdbTransportList
from adb.transport_list.watch.failure import AdbTransportListWatchServerConnectionFailure
from adb.transport_list.watch.generation import AdbTransportListWatchGenerationIssuer
from adb.transport_list.watch.request import AdbTransportListWatchRequest
from adb.transport_list.watch.supervision.policy import AdbTransportListWatchRecoveryPolicy
from adb.transport_list.watch.supervision.recovery import AdbTransportListWatchRecovery
from adb.transport_list.watch.template import AdbTransportListWatchAcquireError
from networking import TcpAddress


class _Capability:
    @property
    def initial(self) -> AdbTransportList:
        return AdbTransportList()

    def updates(self):
        return iter(())


class AdbTransportListWatchRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        issuer = AdbTransportListWatchGenerationIssuer()
        self.generation = issuer.issue()
        self.request = AdbTransportListWatchRequest(TcpAddress("127.0.0.1", 5037))
        self.policy = AdbTransportListWatchRecoveryPolicy(
            retry_initial_seconds=0.001,
            retry_max_seconds=0.001,
            retry_multiplier=1.0,
            retry_jitter_ratio=0.0,
            deferred_retry_seconds=0.001,
            max_attempts=1,
        )

    def test_success_is_acquired(self) -> None:
        recovery = AdbTransportListWatchRecovery(self.policy, _random=lambda: 0.5)
        recovery.begin()
        snapshot = LifecycleSnapshot(
            self.generation,
            self.request,
            _Capability(),
            phase=LifecyclePhase.ACTIVE,
        )

        decision = recovery.decide_after(AcquireSucceeded(snapshot))

        self.assertIsInstance(decision, RecoveryAcquired)

    def test_busy_is_deferred_without_consuming_failure_budget(self) -> None:
        recovery = AdbTransportListWatchRecovery(self.policy, _random=lambda: 0.5)
        recovery.begin()

        decision = recovery.decide_after(LifecycleBusy(LifecyclePhase.ACQUIRING))

        self.assertIsInstance(decision, RecoveryAttempt)
        self.assertEqual(recovery.failed_attempts, 0)

    def test_failed_acquire_preserves_watch_failure_on_exhaustion(self) -> None:
        recovery = AdbTransportListWatchRecovery(self.policy, _random=lambda: 0.5)
        recovery.begin()
        cause = AdbTransportListWatchAcquireError(
            AdbTransportListWatchServerConnectionFailure("connection lost")
        )
        snapshot = LifecycleSnapshot(
            self.generation,
            self.request,
            phase=LifecyclePhase.RELEASE_REQUIRED,
            last_error=cause,
        )

        decision = recovery.decide_after(AcquireFailed(snapshot))

        self.assertIsInstance(decision, RecoveryFailed)
        self.assertIs(decision.cause, cause)
        self.assertEqual(decision.failed_attempts, 1)


if __name__ == "__main__":
    unittest.main()
