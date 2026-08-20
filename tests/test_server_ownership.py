from __future__ import annotations

import threading
import unittest

from adb.managed import AdbManagedRuntime
from adb.server.acquisition import (
    AdbServerAcquirer,
    AdbServerAcquisitionError,
    AdbServerAcquisitionPolicy,
    AdbServerCandidateAttempt,
    AdbServerCandidateOutcome,
    AdbServerLease,
)
from adb.server.endpoint import (
    AdbServerEndpoint,
    EndpointObservation,
    EndpointObservationStatus,
    InMemoryAdbServerEndpointProvisioner,
)
from adb.server.ownership import (
    AdbServerConfigurationConflictError,
    ProcessAdbServerSlot,
    _ProcessAdbServerSlotState,
)
from adb.server.status.model import AdbServerStatus


def _created_lease(endpoint: AdbServerEndpoint) -> AdbServerLease:
    provisioner = InMemoryAdbServerEndpointProvisioner()
    reservation = provisioner.reserve(endpoint=endpoint)
    endpoint_lease = reservation.promote()
    precheck = EndpointObservation(
        endpoint,
        EndpointObservationStatus.NO_LISTENER_OBSERVED,
    )
    verified = EndpointObservation(
        endpoint,
        EndpointObservationStatus.ADB_SERVER_VERIFIED,
        server_status=AdbServerStatus(),
    )
    attempt = AdbServerCandidateAttempt(
        endpoint,
        (precheck,),
        AdbServerCandidateOutcome.CREATED_BY_ACQUISITION,
        verification_observations=(verified,),
    )
    return AdbServerLease(endpoint_lease, AdbServerStatus(), (attempt,))


class _FakeAcquirer:
    def __init__(self, default_endpoint: AdbServerEndpoint) -> None:
        self.default_endpoint = default_endpoint
        self.calls = 0

    def acquire(
        self,
        policy: AdbServerAcquisitionPolicy,
        *,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerLease:
        self.calls += 1
        return _created_lease(endpoint or self.default_endpoint)


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
    ) -> AdbServerLease:
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=2.0):
            raise RuntimeError("test acquisition release was not signaled")
        return _created_lease(endpoint or self.default_endpoint)


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


class AcquisitionOwnershipTests(unittest.TestCase):
    def test_existing_compatible_server_is_never_adopted(self) -> None:
        endpoint = AdbServerEndpoint("localhost", 5037)
        acquirer = AdbServerAcquirer(
            reservation_provider=InMemoryAdbServerEndpointProvisioner(),
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


class ProcessAdbServerSlotTests(unittest.TestCase):
    def test_multiple_slots_share_one_reference_and_one_creation(self) -> None:
        state = _ProcessAdbServerSlotState()
        endpoint = AdbServerEndpoint("localhost", 5038)
        first_acquirer = _FakeAcquirer(endpoint)
        second_acquirer = _FakeAcquirer(AdbServerEndpoint("localhost", 5039))
        first = ProcessAdbServerSlot(first_acquirer, _state=state)
        second = ProcessAdbServerSlot(second_acquirer, _state=state)
        policy = AdbServerAcquisitionPolicy()

        first_ref = first.acquire(policy)
        second_ref = second.acquire(policy)

        self.assertIs(first_ref, second_ref)
        self.assertEqual(first_ref.endpoint, endpoint)
        self.assertEqual(first_acquirer.calls, 1)
        self.assertEqual(second_acquirer.calls, 0)

    def test_concurrent_callers_create_only_once(self) -> None:
        state = _ProcessAdbServerSlotState()
        endpoint = AdbServerEndpoint("localhost", 5040)
        winner = _BlockingAcquirer(endpoint)
        waiter = _FakeAcquirer(endpoint)
        first = ProcessAdbServerSlot(winner, _state=state)
        second = ProcessAdbServerSlot(waiter, _state=state)
        policy = AdbServerAcquisitionPolicy()
        refs: list[object] = []
        errors: list[BaseException] = []

        def acquire(slot: ProcessAdbServerSlot) -> None:
            try:
                refs.append(slot.acquire(policy))
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
        self.assertEqual(len(refs), 2)
        self.assertIs(refs[0], refs[1])
        self.assertEqual(winner.calls, 1)
        self.assertEqual(waiter.calls, 0)

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

    def test_managed_runtime_requires_borrow_reference(self) -> None:
        state = _ProcessAdbServerSlotState()
        endpoint = AdbServerEndpoint("localhost", 5043)
        slot = ProcessAdbServerSlot(_FakeAcquirer(endpoint), _state=state)
        reference = slot.acquire(AdbServerAcquisitionPolicy())

        runtime = AdbManagedRuntime(reference)
        self.assertIs(runtime.server, reference)
        self.assertEqual(runtime.endpoint, endpoint)

        with self.assertRaises(TypeError):
            AdbManagedRuntime(endpoint)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
