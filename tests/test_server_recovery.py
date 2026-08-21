from __future__ import annotations

from datetime import datetime, timedelta
from threading import Condition, Lock
from time import monotonic, sleep
import unittest

from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle.native import AdbServerLaunchError
from adb.server.model import (
    AdbServerAvailability,
    AdbServerFailure,
    AdbServerFailureKind,
    AdbServerObservation,
)
from adb.server.ownership import _ProcessAdbServerOwner
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
    AdbServerRecoveryRetryDue,
)
from adb.transport.signal import (
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingFailure,
    AdbDevicesTrackingStarted,
)
from eventing import EventSubscriptionToken
from scheduling import ScheduleToken


class _FakeNativeHandle:
    def __init__(self, endpoint: AdbServerEndpoint) -> None:
        self._endpoint = endpoint
        self.running = True
        self.close_calls = 0

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self._endpoint

    @property
    def active(self) -> bool:
        return self.running

    def close(self) -> None:
        self.close_calls += 1
        self.running = False


class _ScriptedLauncher:
    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        *,
        fail_on_calls: frozenset[int] = frozenset(),
    ) -> None:
        self.endpoint = endpoint
        self.fail_on_calls = fail_on_calls
        self.calls = 0
        self.handles: list[_FakeNativeHandle] = []

    def launch(self) -> _FakeNativeHandle:
        self.calls += 1
        if self.calls in self.fail_on_calls:
            raise AdbServerLaunchError("scripted launch failure")
        handle = _FakeNativeHandle(self.endpoint)
        self.handles.append(handle)
        return handle


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


def _server_failure(
    kind: AdbServerFailureKind = AdbServerFailureKind.CONNECTION,
    diagnostic: str = "scripted server failure",
) -> AdbServerFailure:
    return AdbServerFailure(kind, diagnostic)


def _available_server(endpoint: AdbServerEndpoint) -> AdbServerObservation:
    return AdbServerObservation(endpoint, AdbServerAvailability.AVAILABLE)


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = monotonic() + timeout
    while not predicate():
        if monotonic() >= deadline:
            raise AssertionError("timed out waiting for condition")
        sleep(0.01)


class AdbServerFailureModelTests(unittest.TestCase):
    def test_unavailable_observation_requires_failure_evidence(self) -> None:
        endpoint = AdbServerEndpoint("127.0.0.1", 5048)

        with self.assertRaises(ValueError):
            AdbServerObservation(endpoint, AdbServerAvailability.UNAVAILABLE)

    def test_available_observation_rejects_failure_evidence(self) -> None:
        endpoint = AdbServerEndpoint("127.0.0.1", 5049)

        with self.assertRaises(ValueError):
            AdbServerObservation(
                endpoint,
                AdbServerAvailability.AVAILABLE,
                _server_failure(),
            )


class ProcessAdbServerRecoveryTests(unittest.TestCase):
    def test_invalidated_owner_is_disposed_and_acquire_creates_fresh_generation(self) -> None:
        endpoint = AdbServerEndpoint("127.0.0.1", 5050)
        launcher = _ScriptedLauncher(endpoint)
        manager = _ProcessAdbServerOwner(launcher)
        first = manager.acquire()

        self.assertTrue(manager.invalidate(first))
        self.assertFalse(first.active)
        self.assertEqual(launcher.handles[0].close_calls, 1)
        self.assertIsNone(manager.active_owner)

        recovered = manager.acquire()
        self.assertIsNot(recovered, first)
        self.assertEqual(recovered.generation, 2)
        self.assertEqual(recovered.endpoint, endpoint)
        self.assertIs(manager.active_owner, recovered)
        self.assertEqual(launcher.calls, 2)

    def test_failed_reacquire_stays_absent_and_next_acquire_retries(self) -> None:
        endpoint = AdbServerEndpoint("127.0.0.1", 5051)
        launcher = _ScriptedLauncher(endpoint, fail_on_calls=frozenset({2}))
        manager = _ProcessAdbServerOwner(launcher)
        first = manager.acquire()
        manager.invalidate(first)

        with self.assertRaises(AdbServerLaunchError):
            manager.acquire()
        self.assertIsNone(manager.active_owner)

        recovered = manager.acquire()
        self.assertEqual(recovered.generation, 2)
        self.assertEqual(launcher.calls, 3)


class AdbServerSupervisorRecoveryTests(unittest.TestCase):
    def test_reconciliation_disposes_old_generation_before_recovery(self) -> None:
        endpoint = AdbServerEndpoint("127.0.0.1", 5052)
        launcher = _ScriptedLauncher(endpoint)
        manager = _ProcessAdbServerOwner(launcher)
        first = manager.acquire()
        bus = _EventBus()
        scheduler = _Scheduler()
        supervisor = AdbServerSupervisor(
            first,
            bus,
            scheduler,
            AdbServerSupervisionPolicy(max_attempts=1),
            _owner_manager=manager,
        )
        supervisor.start(recovery_enabled=True)

        failure = _server_failure(diagnostic="tracker lost server connection")
        bus.publish(AdbServerReconciliationRequested(endpoint, failure))
        bus.wait_for(AdbServerOwnershipRecovered)
        _wait_until(lambda: supervisor.server is not None)
        recovered = supervisor.server
        assert recovered is not None

        self.assertFalse(first.active)
        self.assertEqual(launcher.handles[0].close_calls, 1)
        self.assertIsNot(recovered, first)
        self.assertEqual(recovered.generation, 2)
        self.assertIs(manager.active_owner, recovered)
        self.assertEqual(launcher.calls, 2)
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
        loss_event = bus.events[loss_index]
        assert isinstance(loss_event, AdbServerOwnershipLost)
        self.assertEqual(loss_event.failure, failure)
        supervisor.close()

    def test_launch_failure_exhausts_with_no_active_owner(self) -> None:
        endpoint = AdbServerEndpoint("127.0.0.1", 5053)
        launcher = _ScriptedLauncher(endpoint, fail_on_calls=frozenset({2}))
        manager = _ProcessAdbServerOwner(launcher)
        first = manager.acquire()
        bus = _EventBus()
        scheduler = _Scheduler()
        supervisor = AdbServerSupervisor(
            first,
            bus,
            scheduler,
            AdbServerSupervisionPolicy(max_attempts=1),
            _owner_manager=manager,
        )
        supervisor.start(recovery_enabled=True)

        bus.publish(
            AdbServerReconciliationRequested(
                endpoint,
                _server_failure(diagnostic="server disappeared"),
            )
        )
        exhausted = bus.wait_for(AdbServerRecoveryExhausted)
        assert isinstance(exhausted, AdbServerRecoveryExhausted)
        self.assertEqual(exhausted.failure.kind, AdbServerFailureKind.LAUNCH)
        self.assertEqual(exhausted.failure.diagnostic, "scripted launch failure")

        self.assertFalse(first.active)
        self.assertIsNone(manager.active_owner)
        self.assertIsNone(supervisor.server)
        self.assertEqual(launcher.calls, 2)
        self.assertFalse(
            any(isinstance(event, AdbServerOwnershipRecovered) for event in bus.events)
        )
        supervisor.close()

    def test_scheduled_retry_reacquires_through_same_owner_manager(self) -> None:
        endpoint = AdbServerEndpoint("127.0.0.1", 5054)
        launcher = _ScriptedLauncher(endpoint, fail_on_calls=frozenset({2}))
        manager = _ProcessAdbServerOwner(launcher)
        first = manager.acquire()
        bus = _EventBus()
        scheduler = _Scheduler()
        supervisor = AdbServerSupervisor(
            first,
            bus,
            scheduler,
            AdbServerSupervisionPolicy(
                retry_initial_seconds=0.01,
                retry_max_seconds=0.01,
                retry_jitter_ratio=0.0,
                max_attempts=2,
            ),
            _owner_manager=manager,
        )
        supervisor.start(recovery_enabled=True)

        bus.publish(
            AdbServerReconciliationRequested(
                endpoint,
                _server_failure(diagnostic="server disappeared"),
            )
        )
        _wait_until(lambda: bool(scheduler.scheduled))
        retry_event = scheduler.scheduled[-1][1]
        self.assertIsInstance(retry_event, AdbServerRecoveryRetryDue)
        bus.publish(retry_event)
        bus.wait_for(AdbServerOwnershipRecovered)
        _wait_until(lambda: supervisor.server is not None)

        recovered = supervisor.server
        assert recovered is not None
        self.assertEqual(recovered.generation, 2)
        self.assertEqual(launcher.calls, 3)
        supervisor.close()

    def test_recovery_disabled_still_disposes_current_owner(self) -> None:
        endpoint = AdbServerEndpoint("127.0.0.1", 5055)
        launcher = _ScriptedLauncher(endpoint)
        manager = _ProcessAdbServerOwner(launcher)
        first = manager.acquire()
        bus = _EventBus()
        scheduler = _Scheduler()
        supervisor = AdbServerSupervisor(
            first,
            bus,
            scheduler,
            AdbServerSupervisionPolicy(max_attempts=1),
            _owner_manager=manager,
        )
        supervisor.start(recovery_enabled=False)

        bus.publish(
            AdbServerReconciliationRequested(
                endpoint,
                _server_failure(diagnostic="server disappeared"),
            )
        )

        self.assertFalse(first.active)
        self.assertEqual(launcher.handles[0].close_calls, 1)
        self.assertIsNone(manager.active_owner)
        self.assertIsNone(supervisor.server)
        self.assertEqual(launcher.calls, 1)
        self.assertTrue(
            any(isinstance(event, AdbServerOwnershipLost) for event in bus.events)
        )
        supervisor.close()


class TrackingScopeRecompositionTests(unittest.TestCase):
    def test_server_loss_closes_tracker_and_recovery_creates_a_new_one(self) -> None:
        endpoint = AdbServerEndpoint("127.0.0.1", 5056)
        bus = _EventBus()
        factory = _TrackerFactory()
        supervisor = AdbDevicesTrackingSupervisor(
            endpoint,
            bus,
            AdbDevicesTrackingSupervisionPolicy(episode_timeout_seconds=1.0),
            _tracker_factory=factory,
        )

        self.assertTrue(supervisor.start(_available_server(endpoint)))
        self.assertEqual(len(factory.instances), 1)
        first = factory.instances[0]
        self.assertTrue(first.active)

        bus.publish(AdbServerOwnershipLost(endpoint, _server_failure()))
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

    def test_server_connection_failure_requests_reconciliation_with_evidence(self) -> None:
        endpoint = AdbServerEndpoint("127.0.0.1", 5057)
        bus = _EventBus()
        factory = _TrackerFactory()
        supervisor = AdbDevicesTrackingSupervisor(
            endpoint,
            bus,
            AdbDevicesTrackingSupervisionPolicy(episode_timeout_seconds=1.0),
            _tracker_factory=factory,
        )

        self.assertTrue(supervisor.start(_available_server(endpoint)))
        bus.publish(
            AdbDevicesTrackingFailed(
                endpoint,
                AdbDevicesTrackingFailure.SERVER_CONNECTION,
                "unexpected EOF from ADB server",
            )
        )

        request = next(
            event
            for event in bus.events
            if isinstance(event, AdbServerReconciliationRequested)
        )
        self.assertEqual(request.failure.kind, AdbServerFailureKind.CONNECTION)
        self.assertEqual(request.failure.diagnostic, "unexpected EOF from ADB server")
        supervisor.close()


if __name__ == "__main__":
    unittest.main()
