from __future__ import annotations

from datetime import datetime, timedelta
from threading import Condition, Lock
from time import monotonic, sleep
import unittest

from adb.server.acquisition import (
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
from adb.server.lifecycle import AdbServerAvailability
from adb.server.ownership import ProcessAdbServerSlot, _ProcessAdbServerSlotState
from adb.server.status.model import AdbServerStatus
from adb.supervision.devices_tracking import AdbDevicesTrackingSupervisor
from adb.supervision.model import (
    AdbDevicesTrackingSupervisionPolicy,
    AdbServerSupervisionPolicy,
)
from adb.supervision.server import AdbServerSupervisor
from adb.supervision.signal import (
    AdbServerOwnershipLost,
    AdbServerOwnershipRecovered,
    AdbServerReconciliationRequested,
    AdbServerRecoveryExhausted,
)
from adb.transport.signal import AdbDevicesTrackingStarted
from eventing import EventSubscriptionToken
from scheduling import ScheduleToken


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
    attempt = AdbServerCandidateAttempt(
        endpoint,
        (precheck,),
        AdbServerCandidateOutcome.CREATED_BY_ACQUISITION,
        verification_observations=(verified,),
    )
    return AdbServerAcquisition(endpoint, AdbServerStatus(), (attempt,))


def _occupied_error(endpoint: AdbServerEndpoint) -> AdbServerAcquisitionError:
    observed = EndpointObservation(
        endpoint,
        EndpointObservationStatus.ADB_SERVER_VERIFIED,
        server_status=AdbServerStatus(),
    )
    attempt = AdbServerCandidateAttempt(
        endpoint,
        (observed,),
        AdbServerCandidateOutcome.OCCUPIED,
    )
    return AdbServerAcquisitionError((attempt,))


class _ScriptedAcquirer:
    def __init__(
        self,
        default_endpoint: AdbServerEndpoint,
        *,
        fail_on_calls: frozenset[int] = frozenset(),
    ) -> None:
        self.default_endpoint = default_endpoint
        self.fail_on_calls = fail_on_calls
        self.calls: list[AdbServerEndpoint] = []

    def acquire(
        self,
        policy: AdbServerAcquisitionPolicy,
        *,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerAcquisition:
        resolved = endpoint or self.default_endpoint
        self.calls.append(resolved)
        if len(self.calls) in self.fail_on_calls:
            raise _occupied_error(resolved)
        return _created_acquisition(resolved)


class _EventBus:
    def __init__(self) -> None:
        self._condition = Condition()
        self._next_token = 1
        self._subscriptions: dict[
            EventSubscriptionToken,
            tuple[type[object], object],
        ] = {}
        self.events: list[object] = []

    def publish(self, event: object) -> None:
        with self._condition:
            self.events.append(event)
            subscriptions = tuple(self._subscriptions.values())
            self._condition.notify_all()
        for event_type, handler in subscriptions:
            if isinstance(event, event_type):
                handler(event)  # type: ignore[operator]

    def subscribe(self, event_type, handler) -> EventSubscriptionToken:
        with self._condition:
            token = EventSubscriptionToken(f"test-subscription-{self._next_token}")
            self._next_token += 1
            self._subscriptions[token] = (event_type, handler)
            return token

    def unsubscribe(self, token: EventSubscriptionToken) -> bool:
        with self._condition:
            return self._subscriptions.pop(token, None) is not None

    def wait_for(self, event_type: type[object], timeout: float = 2.0) -> object:
        deadline = monotonic() + timeout
        with self._condition:
            while True:
                for event in self.events:
                    if isinstance(event, event_type):
                        return event
                remaining = deadline - monotonic()
                if remaining <= 0.0:
                    raise AssertionError(f"timed out waiting for {event_type.__name__}")
                self._condition.wait(timeout=remaining)


class _Scheduler:
    def __init__(self) -> None:
        self._lock = Lock()
        self._next_token = 1
        self.scheduled: list[tuple[ScheduleToken, object]] = []
        self.cancelled: list[ScheduleToken] = []

    def _register(self, event: object) -> ScheduleToken:
        with self._lock:
            token = ScheduleToken(f"test-schedule-{self._next_token}")
            self._next_token += 1
            self.scheduled.append((token, event))
            return token

    def schedule_at(self, deadline: datetime, event: object, **kwargs) -> ScheduleToken:
        return self._register(event)

    def schedule_after(self, delay: timedelta, event: object) -> ScheduleToken:
        return self._register(event)

    def schedule_recurring(self, schedule, event: object, **kwargs) -> ScheduleToken:
        return self._register(event)

    def cancel(self, token: ScheduleToken) -> bool:
        with self._lock:
            self.cancelled.append(token)
            return True


class _FakeTracker:
    def __init__(self, endpoint: AdbServerEndpoint, bus: _EventBus) -> None:
        self.endpoint = endpoint
        self.bus = bus
        self.started = False
        self.closed = False

    @property
    def active(self) -> bool:
        return self.started and not self.closed

    def start(self) -> None:
        if self.started or self.closed:
            raise RuntimeError("fake tracker is single-use")
        self.started = True
        self.bus.publish(AdbDevicesTrackingStarted(self.endpoint))

    def close(self) -> None:
        self.closed = True


class _TrackerFactory:
    def __init__(self) -> None:
        self.instances: list[_FakeTracker] = []

    def __call__(self, endpoint: AdbServerEndpoint, bus: _EventBus) -> _FakeTracker:
        tracker = _FakeTracker(endpoint, bus)
        self.instances.append(tracker)
        return tracker


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = monotonic() + timeout
    while not predicate():
        if monotonic() >= deadline:
            raise AssertionError("timed out waiting for condition")
        sleep(0.01)


class ProcessAdbServerRecoveryTests(unittest.TestCase):
    def test_lost_owner_is_removed_and_recovery_creates_fresh_owner(self) -> None:
        state = _ProcessAdbServerSlotState()
        endpoint = AdbServerEndpoint("localhost", 5050)
        acquirer = _ScriptedAcquirer(endpoint)
        slot = ProcessAdbServerSlot(acquirer, _state=state)
        policy = AdbServerAcquisitionPolicy()

        first = slot.acquire(policy, endpoint=endpoint)
        self.assertTrue(first.active)

        self.assertTrue(slot.mark_lost(first))
        self.assertFalse(first.active)
        self.assertIsNone(slot.active_owner)

        recovered = slot.recover(policy)
        self.assertIsNot(recovered, first)
        self.assertEqual(recovered.endpoint, endpoint)
        self.assertTrue(recovered.active)
        self.assertIs(slot.active_owner, recovered)
        self.assertEqual(acquirer.calls, [endpoint, endpoint])

    def test_failed_recovery_stays_lost_and_retries_same_endpoint(self) -> None:
        state = _ProcessAdbServerSlotState()
        endpoint = AdbServerEndpoint("localhost", 5051)
        acquirer = _ScriptedAcquirer(endpoint, fail_on_calls=frozenset({2}))
        slot = ProcessAdbServerSlot(acquirer, _state=state)
        policy = AdbServerAcquisitionPolicy()

        first = slot.acquire(policy, endpoint=endpoint)
        slot.mark_lost(first)

        with self.assertRaises(AdbServerAcquisitionError):
            slot.recover(policy)

        self.assertFalse(first.active)
        self.assertIsNone(slot.active_owner)

        recovered = slot.recover(policy)
        self.assertIsNot(recovered, first)
        self.assertEqual(acquirer.calls, [endpoint, endpoint, endpoint])


class AdbServerSupervisorRecoveryTests(unittest.TestCase):
    def test_reconciliation_destroys_old_owner_before_recovery(self) -> None:
        state = _ProcessAdbServerSlotState()
        endpoint = AdbServerEndpoint("localhost", 5052)
        acquirer = _ScriptedAcquirer(endpoint)
        slot = ProcessAdbServerSlot(acquirer, _state=state)
        first = slot.acquire(AdbServerAcquisitionPolicy(), endpoint=endpoint)
        bus = _EventBus()
        scheduler = _Scheduler()
        supervisor = AdbServerSupervisor(
            first,
            bus,
            slot,
            scheduler,
            AdbServerSupervisionPolicy(max_attempts=1),
        )
        supervisor.start(recovery_enabled=True)

        bus.publish(AdbServerReconciliationRequested(endpoint))
        bus.wait_for(AdbServerOwnershipRecovered)
        _wait_until(lambda: supervisor.server is not None)
        recovered = supervisor.server
        assert recovered is not None

        self.assertFalse(first.active)
        self.assertIsNot(recovered, first)
        self.assertIs(slot.active_owner, recovered)
        self.assertEqual(acquirer.calls, [endpoint, endpoint])
        loss_index = next(
            index
            for index, event in enumerate(bus.events)
            if isinstance(event, AdbServerOwnershipLost)
        )
        recovery_index = next(
            index
            for index, event in enumerate(bus.events)
            if isinstance(event, AdbServerOwnershipRecovered)
        )
        self.assertLess(loss_index, recovery_index)
        supervisor.close()

    def test_existing_listener_conflict_exhausts_with_no_active_owner(self) -> None:
        state = _ProcessAdbServerSlotState()
        endpoint = AdbServerEndpoint("localhost", 5053)
        acquirer = _ScriptedAcquirer(endpoint, fail_on_calls=frozenset({2}))
        slot = ProcessAdbServerSlot(acquirer, _state=state)
        first = slot.acquire(AdbServerAcquisitionPolicy(), endpoint=endpoint)
        bus = _EventBus()
        scheduler = _Scheduler()
        supervisor = AdbServerSupervisor(
            first,
            bus,
            slot,
            scheduler,
            AdbServerSupervisionPolicy(max_attempts=1),
        )
        supervisor.start(recovery_enabled=True)

        bus.publish(AdbServerReconciliationRequested(endpoint))
        bus.wait_for(AdbServerRecoveryExhausted)

        self.assertFalse(first.active)
        self.assertIsNone(slot.active_owner)
        self.assertIsNone(supervisor.server)
        self.assertEqual(acquirer.calls, [endpoint, endpoint])
        self.assertFalse(
            any(isinstance(event, AdbServerOwnershipRecovered) for event in bus.events)
        )
        supervisor.close()

    def test_recovery_disabled_still_invalidates_owner(self) -> None:
        state = _ProcessAdbServerSlotState()
        endpoint = AdbServerEndpoint("localhost", 5054)
        acquirer = _ScriptedAcquirer(endpoint)
        slot = ProcessAdbServerSlot(acquirer, _state=state)
        first = slot.acquire(AdbServerAcquisitionPolicy(), endpoint=endpoint)
        bus = _EventBus()
        scheduler = _Scheduler()
        supervisor = AdbServerSupervisor(
            first,
            bus,
            slot,
            scheduler,
            AdbServerSupervisionPolicy(max_attempts=1),
        )
        supervisor.start(recovery_enabled=False)

        bus.publish(AdbServerReconciliationRequested(endpoint))

        self.assertFalse(first.active)
        self.assertIsNone(slot.active_owner)
        self.assertIsNone(supervisor.server)
        self.assertEqual(acquirer.calls, [endpoint])
        self.assertTrue(
            any(isinstance(event, AdbServerOwnershipLost) for event in bus.events)
        )
        supervisor.close()


class TrackingScopeRecompositionTests(unittest.TestCase):
    def test_server_loss_closes_tracker_and_recovery_creates_a_new_one(self) -> None:
        endpoint = AdbServerEndpoint("localhost", 5055)
        bus = _EventBus()
        factory = _TrackerFactory()
        supervisor = AdbDevicesTrackingSupervisor(
            endpoint,
            bus,
            AdbDevicesTrackingSupervisionPolicy(episode_timeout_seconds=1.0),
            _tracker_factory=factory,
        )

        self.assertTrue(supervisor.start(AdbServerAvailability.AVAILABLE))
        self.assertEqual(len(factory.instances), 1)
        first = factory.instances[0]
        self.assertTrue(first.active)

        bus.publish(AdbServerOwnershipLost(endpoint))
        self.assertTrue(first.closed)
        self.assertFalse(supervisor.tracking_active)

        bus.publish(AdbServerOwnershipRecovered(endpoint))
        _wait_until(lambda: len(factory.instances) == 2)
        _wait_until(lambda: supervisor.tracking_active)
        second = factory.instances[1]

        self.assertIsNot(second, first)
        self.assertTrue(second.active)
        supervisor.close()
        self.assertTrue(second.closed)


if __name__ == "__main__":
    unittest.main()
