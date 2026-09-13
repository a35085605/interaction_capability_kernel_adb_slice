from __future__ import annotations

import unittest
from dataclasses import dataclass

from adb.server import (
    AdbServerGenerationIssuer,
    AdbServerGenerationMismatch,
    AdbServerLifecycleBusy,
    AdbServerPhase,
    AdbServerReleaseAlreadyIdle,
    AdbServerReleaseFailed,
    AdbServerReleaseRequestMismatch,
    AdbServerReleaseSucceeded,
    AdbServerRequest,
    AdbServerSnapshot,
)
from adb.server.supervision.policy import AdbServerReleaseSupervisionPolicy
from adb.server.supervision.release import AdbServerReleaseSupervisor
from networking import TcpAddress


@dataclass
class _ScriptedReleaser:
    results: list[object]

    def __post_init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def release(self, generation, request):
        self.calls.append((generation, request))
        if not self.results:
            raise AssertionError("unexpected release call")
        return self.results.pop(0)


class AdbServerReleaseSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        issuer = AdbServerGenerationIssuer()
        self.generation_1 = issuer.issue()
        self.generation_2 = issuer.issue()
        self.request = AdbServerRequest(TcpAddress("127.0.0.1", 5037))
        self.sleeps: list[float] = []
        self.policy = AdbServerReleaseSupervisionPolicy(retry_seconds=0.25)

    def supervisor(
        self,
        results: list[object],
    ) -> tuple[AdbServerReleaseSupervisor, _ScriptedReleaser]:
        releaser = _ScriptedReleaser(results)
        supervisor = AdbServerReleaseSupervisor(
            releaser,
            policy=self.policy,
            _sleeper=self.sleeps.append,
        )
        return supervisor, releaser

    def release_required_snapshot(self, generation):
        return AdbServerSnapshot(
            generation,
            self.request,
            phase=AdbServerPhase.RELEASE_REQUIRED,
            last_error=RuntimeError("boom"),
        )

    def test_returns_succeeded_unchanged(self) -> None:
        terminal = AdbServerReleaseSucceeded(self.generation_2)
        supervisor, releaser = self.supervisor([terminal])

        result = supervisor.supervise(self.generation_1, self.request)

        self.assertIs(result, terminal)
        self.assertEqual(releaser.calls, [(self.generation_1, self.request)])
        self.assertEqual(self.sleeps, [])

    def test_returns_already_idle_unchanged(self) -> None:
        terminal = AdbServerReleaseAlreadyIdle()
        supervisor, releaser = self.supervisor([terminal])

        result = supervisor.supervise(self.generation_1, self.request)

        self.assertIs(result, terminal)
        self.assertEqual(releaser.calls, [(self.generation_1, self.request)])
        self.assertEqual(self.sleeps, [])

    def test_generation_mismatch_is_terminal_without_following_new_generation(self) -> None:
        terminal = AdbServerGenerationMismatch(self.generation_2)
        supervisor, releaser = self.supervisor([terminal])

        result = supervisor.supervise(self.generation_1, self.request)

        self.assertIs(result, terminal)
        self.assertEqual(releaser.calls, [(self.generation_1, self.request)])
        self.assertEqual(self.sleeps, [])

    def test_request_mismatch_is_terminal_without_releasing_current_request(self) -> None:
        other_request = AdbServerRequest(TcpAddress("127.0.0.1", 5038))
        terminal = AdbServerReleaseRequestMismatch(other_request)
        supervisor, releaser = self.supervisor([terminal])

        result = supervisor.supervise(self.generation_1, self.request)

        self.assertIs(result, terminal)
        self.assertEqual(releaser.calls, [(self.generation_1, self.request)])
        self.assertEqual(self.sleeps, [])

    def test_release_failed_retries_same_lifetime_until_success(self) -> None:
        failure = AdbServerReleaseFailed(self.release_required_snapshot(self.generation_1))
        terminal = AdbServerReleaseSucceeded(self.generation_2)
        supervisor, releaser = self.supervisor([failure, terminal])

        result = supervisor.supervise(self.generation_1, self.request)

        self.assertIs(result, terminal)
        self.assertEqual(
            releaser.calls,
            [
                (self.generation_1, self.request),
                (self.generation_1, self.request),
            ],
        )
        self.assertEqual(self.sleeps, [0.25])

    def test_busy_retries_same_lifetime_without_state_reads(self) -> None:
        terminal = AdbServerReleaseSucceeded(self.generation_2)
        supervisor, releaser = self.supervisor(
            [AdbServerLifecycleBusy(AdbServerPhase.ACQUIRING), terminal]
        )

        result = supervisor.supervise(self.generation_1, self.request)

        self.assertIs(result, terminal)
        self.assertEqual(
            releaser.calls,
            [
                (self.generation_1, self.request),
                (self.generation_1, self.request),
            ],
        )
        self.assertEqual(self.sleeps, [0.25])

    def test_releasing_busy_can_end_with_generation_mismatch_without_following(self) -> None:
        terminal = AdbServerGenerationMismatch(self.generation_2)
        supervisor, releaser = self.supervisor(
            [AdbServerLifecycleBusy(AdbServerPhase.RELEASING), terminal]
        )

        result = supervisor.supervise(self.generation_1, self.request)

        self.assertIs(result, terminal)
        self.assertEqual(
            releaser.calls,
            [
                (self.generation_1, self.request),
                (self.generation_1, self.request),
            ],
        )
        self.assertEqual(self.sleeps, [0.25])

    def test_policy_rejects_non_positive_retry(self) -> None:
        with self.assertRaises(ValueError):
            AdbServerReleaseSupervisionPolicy(retry_seconds=0)


if __name__ == "__main__":
    unittest.main()
