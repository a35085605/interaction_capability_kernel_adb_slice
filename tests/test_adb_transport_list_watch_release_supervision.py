from __future__ import annotations

import unittest

from _lifecycle_new.capability.result import ReleaseFailed, ReleaseSucceeded
from _lifecycle_new.capability.snapshot import LifecyclePhase, LifecycleSnapshot
from adb.transport_list.watch.generation import AdbTransportListWatchGenerationIssuer
from adb.transport_list.watch.request import AdbTransportListWatchRequest
from adb.transport_list.watch.supervision.policy import (
    AdbTransportListWatchReleaseSupervisionPolicy,
)
from adb.transport_list.watch.supervision.release import (
    AdbTransportListWatchReleaseSupervisor,
)
from networking import TcpAddress


class _ScriptedReleaser:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.calls: list[tuple[object, object]] = []

    def release(self, generation, request):
        self.calls.append((generation, request))
        return self.results.pop(0)


class AdbTransportListWatchReleaseSupervisorTests(unittest.TestCase):
    def test_release_failure_retries_same_lifetime_until_success(self) -> None:
        issuer = AdbTransportListWatchGenerationIssuer()
        generation = issuer.issue()
        next_generation = issuer.issue()
        request = AdbTransportListWatchRequest(TcpAddress("127.0.0.1", 5037))
        failed_snapshot = LifecycleSnapshot(
            generation,
            request,
            phase=LifecyclePhase.RELEASE_REQUIRED,
            last_error=OSError("close failed"),
        )
        releaser = _ScriptedReleaser(
            [ReleaseFailed(failed_snapshot), ReleaseSucceeded(next_generation)]
        )
        sleeps: list[float] = []
        supervisor = AdbTransportListWatchReleaseSupervisor(
            releaser,
            policy=AdbTransportListWatchReleaseSupervisionPolicy(retry_seconds=0.25),
            _sleeper=sleeps.append,
        )

        result = supervisor.supervise(generation, request)

        self.assertIsInstance(result, ReleaseSucceeded)
        self.assertEqual(releaser.calls, [(generation, request), (generation, request)])
        self.assertEqual(sleeps, [0.25])


if __name__ == "__main__":
    unittest.main()
