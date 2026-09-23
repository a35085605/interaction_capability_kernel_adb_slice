from __future__ import annotations

import unittest

from adb.runtime.server.availability import (
    AdbServerAvailabilityIncomplete,
    AdbServerAvailabilityPolicy,
    AdbServerAvailabilitySupervisor,
    AdbServerAvailable,
)
from adb.runtime.server.commands import AdbServerCommandPolicy, AdbServerCommands
from adb.runtime.server.runtime import AdbServerRuntime, _snapshot_reader
from adb.server.coordinator import AdbServerLifecycleCoordinator
from adb.server.generation import AdbServerGenerationIssuer
from adb.server.request import AdbServerRequest
from lifecycle.capability.supervision.control import SupervisionStopReason
from lifecycle.resource.result import (
    ResourceAcquireFailed,
    ResourceAcquireSucceeded,
    ResourceCleanupResult,
)
from networking import TcpEndpoint


REQUEST = AdbServerRequest(TcpEndpoint("127.0.0.1", 5037))


class Provider:
    def __init__(self, acquisitions, cleanups=()) -> None:
        self.acquisitions = list(acquisitions)
        self.cleanups = list(cleanups)
        self.acquire_calls = 0
        self.cleanup_calls = 0

    def acquire(self, request):
        self.acquire_calls += 1
        if not self.acquisitions:
            raise AssertionError("unexpected acquire")
        return self.acquisitions.pop(0)

    def cleanup(self, resources):
        self.cleanup_calls += 1
        if not self.cleanups:
            return ResourceCleanupResult.complete()
        return self.cleanups.pop(0)


def availability(provider: Provider) -> AdbServerAvailabilitySupervisor:
    lifecycle = AdbServerLifecycleCoordinator(AdbServerGenerationIssuer(), provider)
    runtime = AdbServerRuntime(
        snapshot=_snapshot_reader(lifecycle),
        commands=AdbServerCommands(
            lifecycle,
            policy=AdbServerCommandPolicy(1.0, 1.0),
            _sleeper=lambda _: None,
        ),
    )
    return AdbServerAvailabilitySupervisor(
        runtime,
        policy=AdbServerAvailabilityPolicy(
            retry_initial_seconds=0.001,
            retry_max_seconds=0.001,
            retry_multiplier=1.0,
            retry_jitter_ratio=0.0,
            deferred_retry_seconds=0.001,
            max_attempts=3,
        ),
        _sleeper=lambda _: None,
        _random=lambda: 0.5,
    )


class ServerAvailabilityTests(unittest.TestCase):
    def test_failed_acquire_that_already_returned_to_idle_counts_once(self) -> None:
        provider = Provider(
            [
                ResourceAcquireFailed(RuntimeError("first attempt"), ()),
                ResourceAcquireSucceeded(()),
            ]
        )

        result = availability(provider).supervise(REQUEST)

        self.assertIsInstance(result, AdbServerAvailable)
        assert isinstance(result, AdbServerAvailable)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.failed_attempts, 1)
        self.assertEqual(len(result.diagnostics), 1)
        self.assertEqual(provider.acquire_calls, 2)

    def test_failed_acquire_cleanup_pending_recovers_before_next_attempt(self) -> None:
        resource = object()
        first_cleanup_error = OSError("retry cleanup")
        provider = Provider(
            [
                ResourceAcquireFailed(RuntimeError("first attempt"), (resource,)),
                ResourceAcquireSucceeded(()),
            ],
            [
                ResourceCleanupResult.retryable(
                    (resource,), errors=(first_cleanup_error,)
                ),
                ResourceCleanupResult.complete(),
            ],
        )

        result = availability(provider).supervise(REQUEST)

        self.assertIsInstance(result, AdbServerAvailable)
        assert isinstance(result, AdbServerAvailable)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.failed_attempts, 1)
        self.assertEqual(provider.cleanup_calls, 2)
        self.assertEqual(result.diagnostics[0].cleanup_errors, (first_cleanup_error,))

    def test_blocked_cleanup_stops_availability_with_host_required(self) -> None:
        resource = object()
        provider = Provider(
            [ResourceAcquireFailed(RuntimeError("failed"), (resource,))],
            [ResourceCleanupResult.blocked((resource,), errors=(OSError("blocked"),))],
        )

        result = availability(provider).supervise(REQUEST)

        self.assertIsInstance(result, AdbServerAvailabilityIncomplete)
        assert isinstance(result, AdbServerAvailabilityIncomplete)
        self.assertIs(result.reason, SupervisionStopReason.HOST_REQUIRED)
        self.assertEqual(provider.cleanup_calls, 1)


if __name__ == "__main__":
    unittest.main()
