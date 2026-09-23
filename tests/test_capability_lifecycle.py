from __future__ import annotations

import unittest

from lifecycle.capability.coordinator import CapabilityLifecycleCoordinator
from lifecycle.capability.result import LifecycleOutcome
from lifecycle.capability.session import DefaultCapabilitySessionFactory
from lifecycle.capability.snapshot import CleanupOrigin, LifecyclePhase
from lifecycle.capability.supervision.control import SupervisionStopReason, SupervisionStopped
from lifecycle.capability.supervision.recovery import RecoverySupervisor
from lifecycle.capability.supervision.release import ReleaseSupervisor, ReleaseDisposition, classify_release_result
from lifecycle.resource.result import (
    ResourceAcquireFailed,
    ResourceAcquireInterrupted,
    ResourceAcquireSucceeded,
    ResourceCleanupResult,
    ResourceCleanupStatus,
)


class SequenceIssuer:
    def __init__(self, *values: object) -> None:
        self._values = list(values)
        self.calls = 0

    def __call__(self) -> int:
        self.calls += 1
        if not self._values:
            raise AssertionError("issuer exhausted")
        value = self._values.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert isinstance(value, int)
        return value


class FakeProvider:
    def __init__(self, acquisitions, cleanups=()) -> None:
        self.acquisitions = list(acquisitions)
        self.cleanups = list(cleanups)
        self.cleanup_calls: list[tuple[object, ...]] = []

    def acquire(self, request: object):
        if not self.acquisitions:
            raise AssertionError("unexpected acquire")
        value = self.acquisitions.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def cleanup(self, resources: tuple[object, ...]):
        self.cleanup_calls.append(resources)
        if not self.cleanups:
            return ResourceCleanupResult.complete()
        value = self.cleanups.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class FakeProjector:
    def __init__(self, values=()) -> None:
        self.values = list(values)
        self.calls = 0

    def project(self, request: object, resources: tuple[object, ...]):
        self.calls += 1
        if self.values:
            value = self.values.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value
        return (request, resources)


def coordinator(issuer, provider, projector=None):
    return CapabilityLifecycleCoordinator(
        issuer,
        DefaultCapabilitySessionFactory(provider, projector or FakeProjector()),
    )


class CountingLifecycle:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.release_calls = 0
        self.recover_calls = 0

    def read(self):
        return self.inner.read()

    def acquire(self, generation, request):
        return self.inner.acquire(generation, request)

    def release(self, generation, request):
        self.release_calls += 1
        return self.inner.release(generation, request)

    def recover(self, generation, request):
        self.recover_calls += 1
        return self.inner.recover(generation, request)


class CapabilityLifecycleTests(unittest.TestCase):
    def test_successful_acquire_publishes_only_after_projection_and_does_not_advance_generation(self) -> None:
        issuer = SequenceIssuer(1, 2)
        resource = object()
        capability = object()
        provider = FakeProvider([ResourceAcquireSucceeded((resource,))])
        lifecycle = coordinator(issuer, provider, FakeProjector([capability]))

        result = lifecycle.acquire(1, "request")

        self.assertIs(result.outcome, LifecycleOutcome.ACQUIRE_SUCCEEDED)
        self.assertIs(result.snapshot.phase, LifecyclePhase.ACTIVE)
        self.assertEqual(result.snapshot.generation, 1)
        self.assertIs(result.snapshot.capability, capability)
        self.assertEqual(issuer.calls, 1)

    def test_acquire_failure_without_resources_rolls_back_and_still_reports_failure(self) -> None:
        issuer = SequenceIssuer(1, 2)
        error = ValueError("prepare failed")
        provider = FakeProvider([ResourceAcquireFailed(error, ())])
        lifecycle = coordinator(issuer, provider)

        result = lifecycle.acquire(1, "request")

        self.assertIs(result.outcome, LifecycleOutcome.ACQUIRE_FAILED)
        self.assertIs(result.snapshot.phase, LifecyclePhase.IDLE)
        self.assertEqual(result.snapshot.generation, 2)
        self.assertIs(result.diagnostics.acquire_error, error)
        self.assertEqual(provider.cleanup_calls, [])

    def test_partial_acquire_failure_rolls_back_once(self) -> None:
        issuer = SequenceIssuer(1, 2)
        resource = object()
        error = RuntimeError("acquire")
        provider = FakeProvider(
            [ResourceAcquireFailed(error, (resource,))],
            [ResourceCleanupResult.complete()],
        )
        lifecycle = coordinator(issuer, provider)

        result = lifecycle.acquire(1, "request")

        self.assertIs(result.outcome, LifecycleOutcome.ACQUIRE_FAILED)
        self.assertIs(result.snapshot.phase, LifecyclePhase.IDLE)
        self.assertEqual(provider.cleanup_calls, [(resource,)])

    def test_projection_failure_is_inside_the_rollback_transaction(self) -> None:
        issuer = SequenceIssuer(1, 2)
        resource = object()
        error = ValueError("projection")
        provider = FakeProvider(
            [ResourceAcquireSucceeded((resource,))],
            [ResourceCleanupResult.complete()],
        )
        lifecycle = coordinator(issuer, provider, FakeProjector([error]))

        result = lifecycle.acquire(1, "request")

        self.assertIs(result.outcome, LifecycleOutcome.ACQUIRE_FAILED)
        self.assertIs(result.diagnostics.acquire_error, error)
        self.assertEqual(provider.cleanup_calls, [(resource,)])
        self.assertIs(result.snapshot.phase, LifecyclePhase.IDLE)

    def test_release_does_not_continue_acquire_origin_cleanup_pending(self) -> None:
        issuer = SequenceIssuer(1, 2)
        resource = object()
        provider = FakeProvider(
            [ResourceAcquireFailed(ValueError("x"), (resource,))],
            [
                ResourceCleanupResult.retryable((resource,)),
                ResourceCleanupResult.complete(),
            ],
        )
        lifecycle = coordinator(issuer, provider)
        lifecycle.acquire(1, "request")

        release = lifecycle.release(1, "request")

        self.assertIs(release.outcome, LifecycleOutcome.NOT_EXECUTED)
        self.assertIs(release.snapshot.phase, LifecyclePhase.CLEANUP_PENDING)
        self.assertEqual(len(provider.cleanup_calls), 1)

    def test_retryable_rollback_blocks_new_acquire_until_recovered(self) -> None:
        issuer = SequenceIssuer(1, 2)
        resource = object()
        provider = FakeProvider(
            [ResourceAcquireFailed(ValueError("x"), (resource,))],
            [
                ResourceCleanupResult.retryable((resource,), errors=(OSError("first"),)),
                ResourceCleanupResult.complete(),
            ],
        )
        lifecycle = coordinator(issuer, provider)

        failed = lifecycle.acquire(1, "request")
        blocked = lifecycle.acquire(1, "request")
        recovered = lifecycle.recover(1, "request")

        self.assertIs(failed.snapshot.phase, LifecyclePhase.CLEANUP_PENDING)
        self.assertIs(failed.snapshot.origin, CleanupOrigin.ACQUIRE)
        self.assertIs(failed.snapshot.cleanup_status, ResourceCleanupStatus.RETRYABLE)
        self.assertIs(blocked.outcome, LifecycleOutcome.NOT_EXECUTED)
        self.assertIs(recovered.outcome, LifecycleOutcome.RECOVERY_COMPLETED)
        self.assertIs(recovered.snapshot.phase, LifecyclePhase.IDLE)
        self.assertEqual(recovered.snapshot.generation, 2)
        self.assertEqual(len(provider.cleanup_calls), 2)

    def test_blocked_cleanup_is_not_retried_and_supervisor_reports_host_required(self) -> None:
        issuer = SequenceIssuer(1, 2)
        resource = object()
        cleanup_error = OSError("ownership unknown")
        provider = FakeProvider(
            [ResourceAcquireFailed(ValueError("x"), (resource,))],
            [ResourceCleanupResult.blocked((resource,), errors=(cleanup_error,))],
        )
        lifecycle = coordinator(issuer, provider)
        lifecycle.acquire(1, "request")

        direct = lifecycle.recover(1, "request")
        supervised = RecoverySupervisor(
            lifecycle,
            _sleeper=lambda _: None,
        ).supervise(1, "request", timeout_seconds=1.0)

        self.assertIs(direct.outcome, LifecycleOutcome.RECOVERY_INCOMPLETE)
        self.assertEqual(len(provider.cleanup_calls), 1)
        self.assertIsInstance(supervised, SupervisionStopped)
        assert isinstance(supervised, SupervisionStopped)
        self.assertIs(supervised.reason, SupervisionStopReason.HOST_REQUIRED)
        self.assertEqual(len(provider.cleanup_calls), 1)

    def test_cleanup_complete_with_error_does_not_repeat_release(self) -> None:
        issuer = SequenceIssuer(1, 2)
        resource = object()
        diagnostic = OSError("close reported late error")
        provider = FakeProvider(
            [ResourceAcquireSucceeded((resource,))],
            [ResourceCleanupResult.complete(errors=(diagnostic,))],
        )
        lifecycle = coordinator(issuer, provider)
        lifecycle.acquire(1, "request")

        released = lifecycle.release(1, "request")

        self.assertIs(released.outcome, LifecycleOutcome.RELEASE_COMPLETED)
        self.assertEqual(released.diagnostics.cleanup_errors, (diagnostic,))
        self.assertEqual(provider.cleanup_calls, [(resource,)])
        self.assertEqual(released.snapshot.generation, 2)

    def test_release_supervisor_starts_release_once_then_delegates_to_recovery(self) -> None:
        issuer = SequenceIssuer(1, 2)
        resource = object()
        provider = FakeProvider(
            [ResourceAcquireSucceeded((resource,))],
            [
                ResourceCleanupResult.retryable((resource,), errors=(OSError("retry"),)),
                ResourceCleanupResult.complete(),
            ],
        )
        inner = coordinator(issuer, provider)
        inner.acquire(1, "request")
        lifecycle = CountingLifecycle(inner)

        result = ReleaseSupervisor(
            lifecycle,
            _sleeper=lambda _: None,
        ).supervise(1, "request", timeout_seconds=1.0)

        self.assertNotIsInstance(result, SupervisionStopped)
        self.assertIs(
            classify_release_result(result, 1, "request"),
            ReleaseDisposition.SUCCEEDED,
        )
        self.assertEqual(lifecycle.release_calls, 1)
        self.assertEqual(lifecycle.recover_calls, 1)
        self.assertEqual(len(provider.cleanup_calls), 2)

    def test_failed_acquire_finalization_pending_preserves_origin_and_primary_error(self) -> None:
        issuer_error = RuntimeError("issuer")
        acquire_error = ValueError("acquire")
        issuer = SequenceIssuer(1, issuer_error, 2)
        provider = FakeProvider([ResourceAcquireFailed(acquire_error, ())])
        lifecycle = coordinator(issuer, provider)

        failed = lifecycle.acquire(1, "request")
        recovered = lifecycle.recover(1, "request")

        self.assertIs(failed.outcome, LifecycleOutcome.ACQUIRE_FAILED)
        self.assertIs(failed.snapshot.phase, LifecyclePhase.FINALIZATION_PENDING)
        self.assertIs(failed.snapshot.origin, CleanupOrigin.ACQUIRE)
        self.assertIs(failed.diagnostics.acquire_error, acquire_error)
        self.assertEqual(failed.diagnostics.finalization_errors, (issuer_error,))
        self.assertIs(recovered.outcome, LifecycleOutcome.RECOVERY_COMPLETED)
        self.assertIs(recovered.origin, CleanupOrigin.ACQUIRE)
        self.assertIs(recovered.diagnostics.acquire_error, acquire_error)
        self.assertEqual(provider.cleanup_calls, [])

    def test_generation_failure_after_cleanup_recovers_without_cleaning_again(self) -> None:
        issuer = SequenceIssuer(1, RuntimeError("issuer"), 2)
        resource = object()
        provider = FakeProvider(
            [ResourceAcquireSucceeded((resource,))],
            [ResourceCleanupResult.complete()],
        )
        lifecycle = coordinator(issuer, provider)
        lifecycle.acquire(1, "request")

        incomplete = lifecycle.release(1, "request")
        recovered = lifecycle.recover(1, "request")

        self.assertIs(incomplete.outcome, LifecycleOutcome.RELEASE_INCOMPLETE)
        self.assertIs(incomplete.snapshot.phase, LifecyclePhase.FINALIZATION_PENDING)
        self.assertIs(incomplete.snapshot.origin, CleanupOrigin.RELEASE)
        self.assertEqual(len(provider.cleanup_calls), 1)
        self.assertIs(recovered.outcome, LifecycleOutcome.RECOVERY_COMPLETED)
        self.assertEqual(len(provider.cleanup_calls), 1)
        self.assertEqual(recovered.snapshot.generation, 2)

    def test_stale_release_cannot_close_new_same_value_request(self) -> None:
        issuer = SequenceIssuer(1, 2, 3)
        first_resource, second_resource = object(), object()
        provider = FakeProvider(
            [
                ResourceAcquireSucceeded((first_resource,)),
                ResourceAcquireSucceeded((second_resource,)),
            ],
            [ResourceCleanupResult.complete(), ResourceCleanupResult.complete()],
        )
        lifecycle = coordinator(issuer, provider)

        lifecycle.acquire(1, "same")
        lifecycle.release(1, "same")
        lifecycle.acquire(2, "same")
        stale = lifecycle.release(1, "same")

        self.assertIs(stale.outcome, LifecycleOutcome.NOT_EXECUTED)
        self.assertIs(stale.snapshot.phase, LifecyclePhase.ACTIVE)
        self.assertEqual(stale.snapshot.generation, 2)
        self.assertEqual(provider.cleanup_calls, [(first_resource,)])

    def test_acquire_keyboard_interrupt_preserves_owner_before_reraise(self) -> None:
        issuer = SequenceIssuer(1, 2)
        resource = object()
        interrupt = KeyboardInterrupt()
        provider = FakeProvider([ResourceAcquireInterrupted(interrupt, (resource,))])
        lifecycle = coordinator(issuer, provider)

        with self.assertRaises(KeyboardInterrupt):
            lifecycle.acquire(1, "request")

        snapshot = lifecycle.read()
        self.assertIs(snapshot.phase, LifecyclePhase.CLEANUP_PENDING)
        self.assertIs(snapshot.cleanup_status, ResourceCleanupStatus.RETRYABLE)
        self.assertEqual(provider.cleanup_calls, [])

    def test_cleanup_keyboard_interrupt_is_saved_as_blocked_before_reraise(self) -> None:
        issuer = SequenceIssuer(1, 2)
        resource = object()
        provider = FakeProvider(
            [ResourceAcquireFailed(ValueError("x"), (resource,))],
            [KeyboardInterrupt()],
        )
        lifecycle = coordinator(issuer, provider)

        with self.assertRaises(KeyboardInterrupt):
            lifecycle.acquire(1, "request")

        snapshot = lifecycle.read()
        self.assertIs(snapshot.phase, LifecyclePhase.CLEANUP_PENDING)
        self.assertIs(snapshot.cleanup_status, ResourceCleanupStatus.BLOCKED)
        self.assertIsNotNone(snapshot.diagnostics)
        assert snapshot.diagnostics is not None
        self.assertIsInstance(snapshot.diagnostics.acquire_error, ValueError)
        self.assertEqual(len(snapshot.diagnostics.cleanup_errors), 1)
        self.assertIsInstance(snapshot.diagnostics.cleanup_errors[0], KeyboardInterrupt)

    def test_acquire_interruption_keeps_prior_operation_error_separate(self) -> None:
        issuer = SequenceIssuer(1, 2)
        resource = object()
        operation_error = ValueError("handshake failed")
        interruption = KeyboardInterrupt()
        provider = FakeProvider(
            [
                ResourceAcquireInterrupted(
                    interruption,
                    (resource,),
                    cleanup_errors=(OSError("pre-cleanup diagnostic"),),
                    operation_error=operation_error,
                )
            ]
        )
        lifecycle = coordinator(issuer, provider)

        with self.assertRaises(KeyboardInterrupt):
            lifecycle.acquire(1, "request")

        snapshot = lifecycle.read()
        self.assertIs(snapshot.phase, LifecyclePhase.CLEANUP_PENDING)
        self.assertIs(snapshot.cleanup_status, ResourceCleanupStatus.RETRYABLE)
        self.assertIsNotNone(snapshot.diagnostics)
        assert snapshot.diagnostics is not None
        self.assertIs(snapshot.diagnostics.acquire_error, operation_error)
        self.assertEqual(len(snapshot.diagnostics.cleanup_errors), 1)
        self.assertIsInstance(snapshot.diagnostics.cleanup_errors[0], OSError)
        self.assertEqual(provider.cleanup_calls, [])


if __name__ == "__main__":
    unittest.main()
