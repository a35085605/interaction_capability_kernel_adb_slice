from __future__ import annotations

import unittest
from dataclasses import dataclass

from _lifecycle_new.capability.result import (
    AcquireAlreadyActive,
    AcquireFailed,
    AcquireReleaseRequired,
    AcquireRequestMismatch,
    AcquireSucceeded,
    GenerationMismatch,
    LifecycleBusy,
)
from _lifecycle_new.capability.snapshot import LifecyclePhase, LifecycleSnapshot
from adb.server.capability import AdbServerCapability
from adb.server.generation import AdbServerGenerationIssuer
from adb.server.request import AdbServerRequest
from adb.server.supervision.acquire import AdbServerAcquireSupervisor
from adb.server.supervision.policy import AdbServerAcquireSupervisionPolicy
from networking import TcpAddress


@dataclass
class _ScriptedAcquirer:
    results: list[object]

    def __post_init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def acquire(self, generation, request):
        self.calls.append((generation, request))
        if not self.results:
            raise AssertionError("unexpected acquire call")
        return self.results.pop(0)


class AdbServerAcquireSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        issuer = AdbServerGenerationIssuer()
        self.generation_1 = issuer.issue()
        self.generation_2 = issuer.issue()
        self.request = AdbServerRequest(TcpAddress("127.0.0.1", 5037))
        self.capability = AdbServerCapability(self.request.server_address)
        self.sleeps: list[float] = []
        self.policy = AdbServerAcquireSupervisionPolicy(deferred_retry_seconds=0.25)

    def supervisor(
        self,
        results: list[object],
    ) -> tuple[AdbServerAcquireSupervisor, _ScriptedAcquirer]:
        acquirer = _ScriptedAcquirer(results)
        supervisor = AdbServerAcquireSupervisor(
            acquirer,
            policy=self.policy,
            _sleeper=self.sleeps.append,
        )
        return supervisor, acquirer

    def active_snapshot(self, generation):
        return LifecycleSnapshot(
            generation,
            self.request,
            self.capability,
            phase=LifecyclePhase.ACTIVE,
        )

    def release_required_snapshot(self, generation):
        return LifecycleSnapshot(
            generation,
            self.request,
            phase=LifecyclePhase.RELEASE_REQUIRED,
            last_error=RuntimeError("boom"),
        )

    def test_returns_succeeded_unchanged(self) -> None:
        terminal = AcquireSucceeded(self.active_snapshot(self.generation_1))
        supervisor, acquirer = self.supervisor([terminal])

        result = supervisor.supervise(self.generation_1, self.request)

        self.assertIs(result, terminal)
        self.assertEqual(acquirer.calls, [(self.generation_1, self.request)])
        self.assertEqual(self.sleeps, [])

    def test_returns_already_active_unchanged(self) -> None:
        terminal = AcquireAlreadyActive(self.active_snapshot(self.generation_1))
        supervisor, _ = self.supervisor([terminal])

        self.assertIs(supervisor.supervise(self.generation_1, self.request), terminal)

    def test_acquire_failed_is_release_required_terminal_without_retry(self) -> None:
        terminal = AcquireFailed(self.release_required_snapshot(self.generation_1))
        supervisor, acquirer = self.supervisor([terminal])

        result = supervisor.supervise(self.generation_1, self.request)

        self.assertIs(result, terminal)
        self.assertEqual(len(acquirer.calls), 1)
        self.assertEqual(self.sleeps, [])

    def test_existing_release_required_is_terminal_without_retry(self) -> None:
        terminal = AcquireReleaseRequired(
            self.release_required_snapshot(self.generation_1)
        )
        supervisor, acquirer = self.supervisor([terminal])

        result = supervisor.supervise(self.generation_1, self.request)

        self.assertIs(result, terminal)
        self.assertEqual(len(acquirer.calls), 1)
        self.assertEqual(self.sleeps, [])

    def test_generation_mismatch_updates_cursor_from_result(self) -> None:
        terminal = AcquireSucceeded(self.active_snapshot(self.generation_2))
        supervisor, acquirer = self.supervisor(
            [GenerationMismatch(self.generation_2), terminal]
        )

        result = supervisor.supervise(self.generation_1, self.request)

        self.assertIs(result, terminal)
        self.assertEqual(
            acquirer.calls,
            [
                (self.generation_1, self.request),
                (self.generation_2, self.request),
            ],
        )
        self.assertEqual(self.sleeps, [0.25])

    def test_busy_and_request_mismatch_are_deferred_without_state_reads(self) -> None:
        other_request = AdbServerRequest(TcpAddress("127.0.0.1", 5038))
        terminal = AcquireSucceeded(self.active_snapshot(self.generation_1))
        supervisor, acquirer = self.supervisor(
            [
                LifecycleBusy(LifecyclePhase.ACQUIRING),
                AcquireRequestMismatch(other_request),
                terminal,
            ]
        )

        result = supervisor.supervise(self.generation_1, self.request)

        self.assertIs(result, terminal)
        self.assertEqual(
            acquirer.calls,
            [
                (self.generation_1, self.request),
                (self.generation_1, self.request),
                (self.generation_1, self.request),
            ],
        )
        self.assertEqual(self.sleeps, [0.25, 0.25])

    def test_policy_rejects_non_positive_retry(self) -> None:
        with self.assertRaises(ValueError):
            AdbServerAcquireSupervisionPolicy(deferred_retry_seconds=0)


if __name__ == "__main__":
    unittest.main()
