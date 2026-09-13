from __future__ import annotations

import unittest

from adb._recovery import RecoveryAcquired, RecoveryAttempt, RecoveryFailed
from adb.server.capability import AdbServerCapability
from adb.server.state import AdbServerPhase, AdbServerSnapshot
from adb.server.generation import AdbServerGenerationIssuer
from adb.server.lifecycle import (
    AdbServerAcquireAlreadyActive,
    AdbServerAcquireFailed,
    AdbServerAcquireReleaseRequired,
    AdbServerAcquireRequestMismatch,
    AdbServerAcquireSucceeded,
    AdbServerGenerationMismatch,
    AdbServerLifecycleBusy,
)
from adb.server.request import AdbServerRequest
from adb.server.supervision.policy import AdbServerRecoveryPolicy
from adb.server.supervision.recovery import AdbServerRecovery
from adb.server import AdbServerAcquireError
from networking import TcpAddress


class AdbServerRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        issuer = AdbServerGenerationIssuer()
        self.generation_1 = issuer.issue()
        self.generation_2 = issuer.issue()
        self.request = AdbServerRequest(TcpAddress("127.0.0.1", 5037))
        self.other_request = AdbServerRequest(TcpAddress("127.0.0.1", 5038))
        self.capability = AdbServerCapability(self.request.server_address)
        self.policy = AdbServerRecoveryPolicy(
            retry_initial_seconds=0.25,
            retry_max_seconds=1.0,
            retry_multiplier=2.0,
            retry_jitter_ratio=0.0,
            deferred_retry_seconds=0.1,
            max_attempts=2,
        )

    def recovery(self) -> AdbServerRecovery:
        return AdbServerRecovery(self.policy, _random=lambda: 0.5)

    def active_snapshot(self, generation):
        return AdbServerSnapshot(
            generation,
            self.request,
            self.capability,
            phase=AdbServerPhase.ACTIVE,
        )

    def release_required_snapshot(self, generation, error):
        return AdbServerSnapshot(
            generation,
            self.request,
            phase=AdbServerPhase.RELEASE_REQUIRED,
            last_error=error,
        )

    def test_success_and_already_active_are_acquired(self) -> None:
        for result in (
            AdbServerAcquireSucceeded(self.active_snapshot(self.generation_1)),
            AdbServerAcquireAlreadyActive(self.active_snapshot(self.generation_1)),
        ):
            recovery = self.recovery()
            recovery.begin()
            self.assertIsInstance(recovery.decide_after(result), RecoveryAcquired)
            self.assertEqual(recovery.failed_attempts, 0)

    def test_generation_busy_and_request_mismatch_are_deferred(self) -> None:
        deferred_results = (
            AdbServerGenerationMismatch(self.generation_2),
            AdbServerLifecycleBusy(AdbServerPhase.ACQUIRING),
            AdbServerAcquireRequestMismatch(self.other_request),
        )
        for result in deferred_results:
            recovery = self.recovery()
            recovery.begin()
            decision = recovery.decide_after(result)
            self.assertIsInstance(decision, RecoveryAttempt)
            self.assertEqual(decision.delay_seconds, self.policy.deferred_retry_seconds)
            self.assertEqual(recovery.failed_attempts, 0)

    def test_failed_acquire_consumes_budget_and_preserves_error_on_exhaustion(self) -> None:
        recovery = self.recovery()
        recovery.begin()
        first_error = AdbServerAcquireError("first failure")
        first = recovery.decide_after(
            AdbServerAcquireFailed(
                self.release_required_snapshot(self.generation_1, first_error)
            )
        )
        self.assertIsInstance(first, RecoveryAttempt)
        self.assertEqual(first.delay_seconds, self.policy.retry_initial_seconds)
        self.assertEqual(recovery.failed_attempts, 1)

        second_error = AdbServerAcquireError("second failure")
        second = recovery.decide_after(
            AdbServerAcquireFailed(
                self.release_required_snapshot(self.generation_2, second_error)
            )
        )
        self.assertIsInstance(second, RecoveryFailed)
        self.assertEqual(second.failed_attempts, 2)
        self.assertIs(second.cause, second_error)

    def test_existing_release_required_consumes_failure_budget(self) -> None:
        recovery = self.recovery()
        recovery.begin()
        error = AdbServerAcquireError("cleanup required")

        decision = recovery.decide_after(
            AdbServerAcquireReleaseRequired(
                self.release_required_snapshot(self.generation_1, error)
            )
        )

        self.assertIsInstance(decision, RecoveryAttempt)
        self.assertEqual(recovery.failed_attempts, 1)

    def test_unexpected_failure_type_is_not_retryable(self) -> None:
        recovery = self.recovery()
        recovery.begin()

        with self.assertRaises(TypeError):
            recovery.decide_after(
                AdbServerAcquireFailed(
                    self.release_required_snapshot(
                        self.generation_1,
                        RuntimeError("unexpected failure"),
                    )
                )
            )


if __name__ == "__main__":
    unittest.main()
