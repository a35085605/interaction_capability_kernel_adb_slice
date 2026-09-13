from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Thread
import unittest

from _lifecycle_new.capability.coordinator import CapabilityLifecycleCoordinator
from _lifecycle_new.capability.result import (
    AcquireFailed,
    AcquireReleaseRequired,
    AcquireSucceeded,
    LifecycleBusy,
    ReleaseFailed,
    ReleaseSucceeded,
)
from _lifecycle_new.capability.snapshot import LifecyclePhase
from _lifecycle_new.resource.result import ResourceAcquireFailed, ResourceAcquireSucceeded


class _Abort(BaseException):
    pass


@dataclass
class _GenerationIssuer:
    outcomes: list[object] = field(default_factory=lambda: [1, 2, 3])

    def __call__(self) -> int:
        if not self.outcomes:
            raise AssertionError("unexpected generation issue")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if not isinstance(outcome, int):
            raise AssertionError("test issuer outcome must be int or BaseException")
        return outcome


@dataclass
class _Provider:
    acquire_outcomes: list[object]
    release_outcomes: list[BaseException | None] = field(default_factory=list)
    acquire_calls: list[object] = field(default_factory=list, init=False)
    release_calls: list[tuple[object, ...]] = field(default_factory=list, init=False)

    def acquire(self, request):
        self.acquire_calls.append(request)
        if not self.acquire_outcomes:
            raise AssertionError("unexpected acquire call")
        outcome = self.acquire_outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def release(self, resources):
        self.release_calls.append(resources)
        if self.release_outcomes:
            outcome = self.release_outcomes.pop(0)
            if outcome is not None:
                raise outcome


@dataclass
class _Projector:
    outcome: object = "capability"
    calls: list[tuple[object, tuple[object, ...]]] = field(default_factory=list, init=False)

    def project(self, request, resources):
        self.calls.append((request, resources))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _BlockingProvider:
    def __init__(self) -> None:
        self.entered = Event()
        self.finish = Event()

    def acquire(self, request):
        self.entered.set()
        if not self.finish.wait(2.0):
            raise AssertionError("timed out waiting to finish acquire")
        return ResourceAcquireSucceeded(("resource",))

    def release(self, resources):
        return None


class CapabilityLifecycleCoordinatorTests(unittest.TestCase):
    def coordinator(self, provider, *, issuer=None, projector=None):
        return CapabilityLifecycleCoordinator(
            _GenerationIssuer() if issuer is None else issuer,
            provider,
            _Projector() if projector is None else projector,
        )

    def test_successful_acquire_and_release_advance_generation(self) -> None:
        provider = _Provider([ResourceAcquireSucceeded(("resource",))])
        coordinator = self.coordinator(provider)
        idle = coordinator.read()

        acquired = coordinator.acquire(idle.generation, "request")

        self.assertIsInstance(acquired, AcquireSucceeded)
        self.assertEqual(acquired.snapshot.phase, LifecyclePhase.ACTIVE)
        self.assertEqual(acquired.snapshot.capability, "capability")

        released = coordinator.release(idle.generation, "request")

        self.assertIsInstance(released, ReleaseSucceeded)
        self.assertEqual(provider.release_calls, [("resource",)])
        self.assertEqual(coordinator.read().generation, released.next_generation)
        self.assertEqual(coordinator.read().phase, LifecyclePhase.IDLE)

    def test_acquire_failure_retains_resources_until_explicit_release(self) -> None:
        error = RuntimeError("acquire failed")
        provider = _Provider([ResourceAcquireFailed(error, ("partial",))])
        coordinator = self.coordinator(provider)
        generation = coordinator.read().generation

        failed = coordinator.acquire(generation, "request")

        self.assertIsInstance(failed, AcquireFailed)
        self.assertIs(failed.snapshot.last_error, error)
        self.assertEqual(failed.snapshot.phase, LifecyclePhase.RELEASE_REQUIRED)
        self.assertIsInstance(
            coordinator.acquire(generation, "request"),
            AcquireReleaseRequired,
        )

        released = coordinator.release(generation, "request")

        self.assertIsInstance(released, ReleaseSucceeded)
        self.assertEqual(provider.release_calls, [("partial",)])

    def test_projection_failure_retains_acquired_resources(self) -> None:
        error = RuntimeError("projection failed")
        provider = _Provider([ResourceAcquireSucceeded(("resource",))])
        coordinator = self.coordinator(provider, projector=_Projector(error))
        generation = coordinator.read().generation

        failed = coordinator.acquire(generation, "request")

        self.assertIsInstance(failed, AcquireFailed)
        self.assertIs(failed.snapshot.last_error, error)
        coordinator.release(generation, "request")
        self.assertEqual(provider.release_calls, [("resource",)])

    def test_release_failure_preserves_resources_for_retry(self) -> None:
        cleanup_error = OSError("cleanup failed")
        provider = _Provider(
            [ResourceAcquireSucceeded(("resource",))],
            release_outcomes=[cleanup_error, None],
        )
        coordinator = self.coordinator(provider)
        generation = coordinator.read().generation
        coordinator.acquire(generation, "request")

        failed = coordinator.release(generation, "request")

        self.assertIsInstance(failed, ReleaseFailed)
        self.assertIs(failed.snapshot.last_error, cleanup_error)
        self.assertEqual(failed.snapshot.phase, LifecyclePhase.RELEASE_REQUIRED)

        succeeded = coordinator.release(generation, "request")

        self.assertIsInstance(succeeded, ReleaseSucceeded)
        self.assertEqual(
            provider.release_calls,
            [("resource",), ("resource",)],
        )

    def test_generation_issue_failure_after_cleanup_does_not_cleanup_twice(self) -> None:
        issue_error = RuntimeError("issuer failed")
        issuer = _GenerationIssuer([1, issue_error, 2])
        provider = _Provider([ResourceAcquireSucceeded(("resource",))])
        coordinator = self.coordinator(provider, issuer=issuer)
        generation = coordinator.read().generation
        coordinator.acquire(generation, "request")

        failed = coordinator.release(generation, "request")

        self.assertIsInstance(failed, ReleaseFailed)
        self.assertIs(failed.snapshot.last_error, issue_error)
        self.assertEqual(provider.release_calls, [("resource",)])

        succeeded = coordinator.release(generation, "request")

        self.assertIsInstance(succeeded, ReleaseSucceeded)
        self.assertEqual(provider.release_calls, [("resource",)])

    def test_non_exception_interruption_is_recorded_before_reraise(self) -> None:
        interruption = _Abort("stop")
        provider = _Provider([interruption])
        coordinator = self.coordinator(provider)
        generation = coordinator.read().generation

        with self.assertRaises(_Abort) as raised:
            coordinator.acquire(generation, "request")

        self.assertIs(raised.exception, interruption)
        snapshot = coordinator.read()
        self.assertEqual(snapshot.phase, LifecyclePhase.RELEASE_REQUIRED)
        self.assertIs(snapshot.last_error, interruption)

    def test_invalid_provider_result_becomes_release_required_failure(self) -> None:
        provider = _Provider([object()])
        coordinator = self.coordinator(provider)
        generation = coordinator.read().generation

        failed = coordinator.acquire(generation, "request")

        self.assertIsInstance(failed, AcquireFailed)
        self.assertIsInstance(failed.snapshot.last_error, TypeError)
        self.assertEqual(failed.snapshot.phase, LifecyclePhase.RELEASE_REQUIRED)
        coordinator.release(generation, "request")
        self.assertEqual(provider.release_calls, [])

    def test_concurrent_calls_observe_acquiring_as_busy(self) -> None:
        provider = _BlockingProvider()
        coordinator = self.coordinator(provider)
        generation = coordinator.read().generation
        results: list[object] = []

        worker = Thread(
            target=lambda: results.append(coordinator.acquire(generation, "request")),
            daemon=True,
        )
        worker.start()
        self.assertTrue(provider.entered.wait(2.0))

        self.assertEqual(coordinator.read().phase, LifecyclePhase.ACQUIRING)
        busy_acquire = coordinator.acquire(generation, "request")
        busy_release = coordinator.release(generation, "request")
        self.assertIsInstance(busy_acquire, LifecycleBusy)
        self.assertIsInstance(busy_release, LifecycleBusy)
        self.assertEqual(busy_acquire.phase, LifecyclePhase.ACQUIRING)
        self.assertEqual(busy_release.phase, LifecyclePhase.ACQUIRING)

        provider.finish.set()
        worker.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], AcquireSucceeded)


if __name__ == "__main__":
    unittest.main()
