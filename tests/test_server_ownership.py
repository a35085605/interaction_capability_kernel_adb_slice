from __future__ import annotations

from datetime import datetime, timezone
import threading
import unittest

from adb.managed import AdbManagedRuntime
from adb.server.acquisition import (
    AdbServerAcquirer,
    AdbServerAcquisition,
    AdbServerAcquisitionError,
    AdbServerAcquisitionPolicy,
    AdbServerCandidateAttempt,
    AdbServerCandidateOutcome,
)
from adb.server.endpoint import (
    AdbServerEndpoint,
    EndpointObservation,
    EndpointObservationStatus,
)
from adb.server.lifecycle.command import AdbServerStop
from adb.server.lifecycle.creation import (
    AdbServerCreationAttempt,
    AdbServerCreationEvidence,
)
from adb.server.ownership import (
    AdbServerConfigurationConflictError,
    AdbServerOwnershipLostError,
    ProcessAdbServerSlot,
    _ProcessAdbServerSlotState,
)
from adb.server.status.model import AdbServerStatus
from native_attempt import NativeAttemptResult, NativeAttemptStatus, NativeCompletionScope


def _native_result(
    status: NativeAttemptStatus,
    completion_scope: NativeCompletionScope | None,
) -> NativeAttemptResult:
    now = datetime.now(timezone.utc)
    return NativeAttemptResult(
        status=status,
        completion_scope=completion_scope,
        backend_id="test",
        started_at=now,
        finished_at=now,
    )


def _created_acquisition(endpoint: AdbServerEndpoint) -> AdbServerAcquisition:
    precheck = EndpointObservation(
        endpoint,
        EndpointObservationStatus.NO_LISTENER_OBSERVED,
    )
    verified = EndpointObservation(
        endpoint,
        EndpointObservationStatus.ADB_SERVER_VERIFIED,
        server_status=AdbServerStatus(),
    )
    creation = AdbServerCreationAttempt(
        endpoint,
        AdbServerCreationEvidence.CREATED_BY_ATTEMPT,
        _native_result(NativeAttemptStatus.SUCCEEDED, NativeCompletionScope.PROCESS_EXIT),
    )
    attempt = AdbServerCandidateAttempt(
        endpoint,
        (precheck,),
        AdbServerCandidateOutcome.CREATED_BY_ACQUISITION,
        creation,
        (verified,),
    )
    return AdbServerAcquisition(endpoint, AdbServerStatus(), (attempt,))


def _mutating_error(endpoint: AdbServerEndpoint) -> AdbServerAcquisitionError:
    precheck = EndpointObservation(
        endpoint,
        EndpointObservationStatus.NO_LISTENER_OBSERVED,
    )
    creation = AdbServerCreationAttempt(
        endpoint,
        AdbServerCreationEvidence.INDETERMINATE,
        _native_result(NativeAttemptStatus.TIMED_OUT, None),
    )
    attempt = AdbServerCandidateAttempt(
        endpoint,
        (precheck,),
        AdbServerCandidateOutcome.CREATION_NOT_CONFIRMED,
        creation,
    )
    return AdbServerAcquisitionError((attempt,))


class _FakeAcquirer:
    def __init__(self, default_endpoint: AdbServerEndpoint) -> None:
        self.default_endpoint = default_endpoint
        self.calls: list[AdbServerEndpoint] = []

    def acquire(
        self,
        policy: AdbServerAcquisitionPolicy,
        *,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerAcquisition:
        resolved = endpoint or self.default_endpoint
        self.calls.append(resolved)
        return _created_acquisition(resolved)


class _BlockingAcquirer(_FakeAcquirer):
    def __init__(self, default_endpoint: AdbServerEndpoint) -> None:
        super().__init__(default_endpoint)
        self.entered = threading.Event()
        self.release = threading.Event()

    def acquire(
        self,
        policy: AdbServerAcquisitionPolicy,
        *,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerAcquisition:
        resolved = endpoint or self.default_endpoint
        self.calls.append(resolved)
        self.entered.set()
        if not self.release.wait(timeout=2.0):
            raise RuntimeError("test acquisition release was not signaled")
        return _created_acquisition(resolved)


class _MutatingThenCreatedAcquirer(_FakeAcquirer):
    def acquire(
        self,
        policy: AdbServerAcquisitionPolicy,
        *,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerAcquisition:
        resolved = endpoint or self.default_endpoint
        self.calls.append(resolved)
        if len(self.calls) == 1:
            raise _mutating_error(resolved)
        return _created_acquisition(resolved)


class _ExistingServerObserver:
    def observe(self, endpoint: AdbServerEndpoint) -> EndpointObservation:
        return EndpointObservation(
            endpoint,
            EndpointObservationStatus.ADB_SERVER_VERIFIED,
            server_status=AdbServerStatus(),
        )


class _UnexpectedCreator:
    def create(self, endpoint: AdbServerEndpoint):
        raise AssertionError("existing server must never reach creation")


class _SequenceAllocator:
    def __init__(self, endpoints: tuple[AdbServerEndpoint, ...]) -> None:
        self.endpoints = endpoints
        self.calls: list[frozenset[AdbServerEndpoint]] = []

    def allocate(
        self,
        excluded_endpoints: frozenset[AdbServerEndpoint],
    ) -> AdbServerEndpoint:
        self.calls.append(excluded_endpoints)
        for endpoint in self.endpoints:
            if endpoint not in excluded_endpoints:
                return endpoint
        raise RuntimeError("test allocator exhausted")


class _ScriptedObserver:
    def __init__(
        self,
        scripts: dict[AdbServerEndpoint, list[EndpointObservationStatus]],
    ) -> None:
        self.scripts = {endpoint: list(statuses) for endpoint, statuses in scripts.items()}
        self.calls: list[AdbServerEndpoint] = []

    def observe(self, endpoint: AdbServerEndpoint) -> EndpointObservation:
        self.calls.append(endpoint)
        statuses = self.scripts[endpoint]
        status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
        server_status = AdbServerStatus() if status is EndpointObservationStatus.ADB_SERVER_VERIFIED else None
        return EndpointObservation(endpoint, status, server_status=server_status)


class _ScriptedCreator:
    def __init__(
        self,
        evidence: dict[AdbServerEndpoint, AdbServerCreationEvidence],
    ) -> None:
        self.evidence = evidence
        self.calls: list[AdbServerEndpoint] = []

    def create(self, endpoint: AdbServerEndpoint) -> AdbServerCreationAttempt:
        self.calls.append(endpoint)
        evidence = self.evidence[endpoint]
        if evidence is AdbServerCreationEvidence.CREATED_BY_ATTEMPT:
            result = _native_result(
                NativeAttemptStatus.SUCCEEDED,
                NativeCompletionScope.PROCESS_EXIT,
            )
        elif evidence is AdbServerCreationEvidence.NOT_CREATED:
            result = _native_result(
                NativeAttemptStatus.FAILED,
                NativeCompletionScope.PROCESS_EXIT,
            )
        else:
            result = _native_result(NativeAttemptStatus.TIMED_OUT, None)
        return AdbServerCreationAttempt(endpoint, evidence, result)


class _FakeStopper:
    def __init__(self, result: NativeAttemptResult) -> None:
        self.result = result
        self.operations: list[AdbServerStop] = []

    def stop(self, operation: AdbServerStop) -> NativeAttemptResult:
        self.operations.append(operation)
        return self.result


class AcquisitionOwnershipTests(unittest.TestCase):
    def test_existing_compatible_server_is_never_adopted(self) -> None:
        endpoint = AdbServerEndpoint("localhost", 5037)
        acquirer = AdbServerAcquirer(
            observer=_ExistingServerObserver(),
            creator=_UnexpectedCreator(),
        )

        with self.assertRaises(AdbServerAcquisitionError) as raised:
            acquirer.acquire(AdbServerAcquisitionPolicy(), endpoint=endpoint)

        self.assertEqual(len(raised.exception.attempts), 1)
        self.assertIs(
            raised.exception.attempts[0].outcome,
            AdbServerCandidateOutcome.OCCUPIED,
        )
        self.assertIsNone(raised.exception.pinned_endpoint)

    def test_verification_failure_stops_candidate_search_and_pins_endpoint(self) -> None:
        first = AdbServerEndpoint("localhost", 5060)
        second = AdbServerEndpoint("localhost", 5061)
        allocator = _SequenceAllocator((first, second))
        observer = _ScriptedObserver(
            {first: [EndpointObservationStatus.NO_LISTENER_OBSERVED, EndpointObservationStatus.INDETERMINATE]}
        )
        creator = _ScriptedCreator({first: AdbServerCreationEvidence.CREATED_BY_ATTEMPT})
        acquirer = AdbServerAcquirer(
            allocator,
            observer,
            creator,
            _monotonic=lambda: 0.0,
            _sleep=lambda _: None,
        )

        with self.assertRaises(AdbServerAcquisitionError) as raised:
            acquirer.acquire(
                AdbServerAcquisitionPolicy(
                    max_candidates=2,
                    verification_timeout_seconds=0.01,
                    probe_interval_seconds=0.01,
                )
            )

        self.assertEqual(creator.calls, [first])
        self.assertEqual(len(allocator.calls), 1)
        self.assertEqual(raised.exception.pinned_endpoint, first)
        self.assertIs(
            raised.exception.attempts[-1].outcome,
            AdbServerCandidateOutcome.VERIFICATION_FAILED,
        )

    def test_indeterminate_creation_stops_candidate_search_and_pins_endpoint(self) -> None:
        first = AdbServerEndpoint("localhost", 5062)
        second = AdbServerEndpoint("localhost", 5063)
        allocator = _SequenceAllocator((first, second))
        observer = _ScriptedObserver(
            {first: [EndpointObservationStatus.NO_LISTENER_OBSERVED]}
        )
        creator = _ScriptedCreator({first: AdbServerCreationEvidence.INDETERMINATE})
        acquirer = AdbServerAcquirer(allocator, observer, creator)

        with self.assertRaises(AdbServerAcquisitionError) as raised:
            acquirer.acquire(AdbServerAcquisitionPolicy(max_candidates=2))

        self.assertEqual(creator.calls, [first])
        self.assertEqual(len(allocator.calls), 1)
        self.assertEqual(raised.exception.pinned_endpoint, first)

    def test_confirmed_not_created_may_advance_to_next_candidate(self) -> None:
        first = AdbServerEndpoint("localhost", 5064)
        second = AdbServerEndpoint("localhost", 5065)
        allocator = _SequenceAllocator((first, second))
        observer = _ScriptedObserver(
            {
                first: [EndpointObservationStatus.NO_LISTENER_OBSERVED],
                second: [
                    EndpointObservationStatus.NO_LISTENER_OBSERVED,
                    EndpointObservationStatus.ADB_SERVER_VERIFIED,
                ],
            }
        )
        creator = _ScriptedCreator(
            {
                first: AdbServerCreationEvidence.NOT_CREATED,
                second: AdbServerCreationEvidence.CREATED_BY_ATTEMPT,
            }
        )
        acquirer = AdbServerAcquirer(allocator, observer, creator)

        acquired = acquirer.acquire(AdbServerAcquisitionPolicy(max_candidates=2))

        self.assertEqual(acquired.endpoint, second)
        self.assertEqual(creator.calls, [first, second])
        self.assertEqual(len(allocator.calls), 2)


class ProcessAdbServerSlotTests(unittest.TestCase):
    def test_multiple_slots_share_one_owner_and_one_creation(self) -> None:
        state = _ProcessAdbServerSlotState()
        endpoint = AdbServerEndpoint("localhost", 5038)
        first_acquirer = _FakeAcquirer(endpoint)
        second_acquirer = _FakeAcquirer(AdbServerEndpoint("localhost", 5039))
        first = ProcessAdbServerSlot(first_acquirer, _state=state)
        second = ProcessAdbServerSlot(second_acquirer, _state=state)
        policy = AdbServerAcquisitionPolicy()

        first_owner = first.acquire(policy)
        second_owner = second.acquire(policy)

        self.assertIs(first_owner, second_owner)
        self.assertEqual(first_owner.endpoint, endpoint)
        self.assertEqual(first_acquirer.calls, [endpoint])
        self.assertEqual(second_acquirer.calls, [])

    def test_concurrent_callers_create_only_once(self) -> None:
        state = _ProcessAdbServerSlotState()
        endpoint = AdbServerEndpoint("localhost", 5040)
        winner = _BlockingAcquirer(endpoint)
        waiter = _FakeAcquirer(endpoint)
        first = ProcessAdbServerSlot(winner, _state=state)
        second = ProcessAdbServerSlot(waiter, _state=state)
        policy = AdbServerAcquisitionPolicy()
        owners: list[object] = []
        errors: list[BaseException] = []

        def acquire(slot: ProcessAdbServerSlot) -> None:
            try:
                owners.append(slot.acquire(policy))
            except BaseException as exc:  # pragma: no cover - assertion aid
                errors.append(exc)

        first_thread = threading.Thread(target=acquire, args=(first,))
        first_thread.start()
        self.assertTrue(winner.entered.wait(timeout=1.0))
        second_thread = threading.Thread(target=acquire, args=(second,))
        second_thread.start()
        winner.release.set()
        first_thread.join(timeout=2.0)
        second_thread.join(timeout=2.0)

        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(owners), 2)
        self.assertIs(owners[0], owners[1])
        self.assertEqual(winner.calls, [endpoint])
        self.assertEqual(waiter.calls, [])

    def test_different_explicit_endpoint_conflicts_after_activation(self) -> None:
        state = _ProcessAdbServerSlotState()
        endpoint = AdbServerEndpoint("localhost", 5041)
        slot = ProcessAdbServerSlot(_FakeAcquirer(endpoint), _state=state)
        policy = AdbServerAcquisitionPolicy()
        slot.acquire(policy, endpoint=endpoint)

        with self.assertRaises(AdbServerConfigurationConflictError):
            slot.acquire(
                policy,
                endpoint=AdbServerEndpoint("localhost", 5042),
            )

    def test_mutating_initial_failure_pins_endpoint_for_recovery(self) -> None:
        state = _ProcessAdbServerSlotState()
        endpoint = AdbServerEndpoint("localhost", 5066)
        acquirer = _MutatingThenCreatedAcquirer(endpoint)
        slot = ProcessAdbServerSlot(acquirer, _state=state)
        policy = AdbServerAcquisitionPolicy()

        with self.assertRaises(AdbServerAcquisitionError):
            slot.acquire(policy)
        with self.assertRaises(AdbServerOwnershipLostError):
            slot.acquire(policy, endpoint=endpoint)

        recovered = slot.recover(policy)
        self.assertEqual(recovered.endpoint, endpoint)
        self.assertEqual(acquirer.calls, [endpoint, endpoint])

    def test_owned_close_fences_owner_and_keeps_same_endpoint_for_recovery(self) -> None:
        state = _ProcessAdbServerSlotState()
        endpoint = AdbServerEndpoint("localhost", 5067)
        acquirer = _FakeAcquirer(endpoint)
        slot = ProcessAdbServerSlot(acquirer, _state=state)
        owner = slot.acquire(AdbServerAcquisitionPolicy(), endpoint=endpoint)
        failed_stop = _native_result(
            NativeAttemptStatus.FAILED,
            NativeCompletionScope.PROCESS_EXIT,
        )
        stopper = _FakeStopper(failed_stop)

        result = slot.close(owner, stopper)

        self.assertIs(result, failed_stop)
        self.assertFalse(owner.active)
        self.assertIsNone(slot.active_owner)
        self.assertEqual(stopper.operations, [AdbServerStop(endpoint)])
        recovered = slot.recover(AdbServerAcquisitionPolicy())
        self.assertEqual(recovered.endpoint, endpoint)
        self.assertIsNot(recovered, owner)
        self.assertEqual(acquirer.calls, [endpoint, endpoint])

    def test_managed_runtime_requires_owned_server(self) -> None:
        state = _ProcessAdbServerSlotState()
        endpoint = AdbServerEndpoint("localhost", 5043)
        slot = ProcessAdbServerSlot(_FakeAcquirer(endpoint), _state=state)
        owner = slot.acquire(AdbServerAcquisitionPolicy())

        runtime = AdbManagedRuntime(owner)
        self.assertIs(runtime.server, owner)
        self.assertEqual(runtime.endpoint, endpoint)

        with self.assertRaises(TypeError):
            AdbManagedRuntime(endpoint)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
