from __future__ import annotations

from datetime import datetime, timedelta
from threading import Event, Lock
import unittest

from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle.native import AdbServerCloseError
from adb.server.model import AdbServerConnectionFailure
from adb.server.ownership import _ProcessAdbServerOwner
from adb.supervision.model import AdbServerSupervisionPolicy
from adb.supervision.server import AdbServerSupervisor
from adb.supervision.signal import (
    AdbServerNativeCloseCompleted,
    AdbServerNativeCloseUnproven,
    AdbServerOwnershipLost,
    AdbServerOwnershipRetired,
    AdbServerReconciliationRequested,
)
from eventing import EventSubscriptionToken
from scheduling import MisfirePolicy, ScheduleToken


class _BlockingHandle:
    def __init__(self, endpoint: AdbServerEndpoint, *, block_close: bool = False) -> None:
        self._endpoint = endpoint
        self._active = True
        self.block_close = block_close
        self.fail_close = False
        self.close_started = Event()
        self.allow_close = Event()

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self._endpoint

    @property
    def active(self) -> bool:
        return self._active

    def close(self) -> None:
        self.close_started.set()
        if self.fail_close:
            raise AdbServerCloseError("termination not proven")
        if self.block_close:
            if not self.allow_close.wait(2.0):
                raise RuntimeError("test close gate timed out")
        self._active = False


class _Launcher:
    def __init__(self, endpoint: AdbServerEndpoint, *, block_first_close: bool = False) -> None:
        self.endpoint = endpoint
        self.block_first_close = block_first_close
        self.handles: list[_BlockingHandle] = []

    def launch(self) -> _BlockingHandle:
        handle = _BlockingHandle(
            self.endpoint,
            block_close=self.block_first_close and not self.handles,
        )
        self.handles.append(handle)
        return handle


class _RecordingBus:
    def __init__(self) -> None:
        self._lock = Lock()
        self.events: list[object] = []
        self._handlers: dict[str, tuple[type[object], object]] = {}
        self._next = 0
        self.close_completed = Event()
        self.close_unproven = Event()

    def publish(self, event: object) -> None:
        with self._lock:
            self.events.append(event)
            handlers = [
                handler
                for event_type, handler in self._handlers.values()
                if isinstance(event, event_type)
            ]
        if isinstance(event, AdbServerNativeCloseCompleted):
            self.close_completed.set()
        if isinstance(event, AdbServerNativeCloseUnproven):
            self.close_unproven.set()
        for handler in handlers:
            handler(event)

    def subscribe(self, event_type, handler) -> EventSubscriptionToken:
        with self._lock:
            self._next += 1
            token = EventSubscriptionToken(f"subscription-{self._next}")
            self._handlers[token.value] = (event_type, handler)
            return token

    def unsubscribe(self, token: EventSubscriptionToken) -> bool:
        with self._lock:
            return self._handlers.pop(token.value, None) is not None


class _Scheduler:
    def __init__(self) -> None:
        self._next = 0

    def _token(self) -> ScheduleToken:
        self._next += 1
        return ScheduleToken(f"schedule-{self._next}")

    def schedule_at(
        self,
        deadline: datetime,
        event: object,
        *,
        misfire_policy: MisfirePolicy = MisfirePolicy.FIRE_ONCE,
    ) -> ScheduleToken:
        return self._token()

    def schedule_after(self, delay: timedelta, event: object) -> ScheduleToken:
        return self._token()

    def schedule_recurring(
        self,
        schedule,
        event: object,
        *,
        misfire_policy: MisfirePolicy = MisfirePolicy.FIRE_ONCE,
    ) -> ScheduleToken:
        return self._token()

    def cancel(self, token: ScheduleToken) -> bool:
        return True


class ServerSupervisionTests(unittest.TestCase):
    def test_retirement_is_published_before_blocking_native_close_completes(self) -> None:
        endpoint = AdbServerEndpoint("127.0.0.1", 5037)
        launcher = _Launcher(endpoint, block_first_close=True)
        owner = _ProcessAdbServerOwner(launcher)
        server = owner.acquire()
        handle = launcher.handles[-1]
        bus = _RecordingBus()
        supervisor = AdbServerSupervisor(
            server,
            bus,
            _Scheduler(),
            AdbServerSupervisionPolicy(),
            _owner_manager=owner,
        )
        supervisor.start(recovery_enabled=False)

        supervisor.reconcile(AdbServerConnectionFailure("connection lost"))
        self.assertTrue(handle.close_started.wait(1.0))

        self.assertIsNone(supervisor.server)
        self.assertIsNone(owner.active_owner)
        self.assertIsInstance(bus.events[0], AdbServerOwnershipRetired)
        self.assertIsInstance(bus.events[1], AdbServerOwnershipLost)
        retired = bus.events[0]
        lost = bus.events[1]
        assert isinstance(retired, AdbServerOwnershipRetired)
        assert isinstance(lost, AdbServerOwnershipLost)
        self.assertEqual(retired.generation, server.generation)
        self.assertEqual(lost.generation, server.generation)
        self.assertFalse(bus.close_completed.is_set())

        handle.allow_close.set()
        self.assertTrue(bus.close_completed.wait(1.0))
        supervisor.close()


    def test_close_unproven_keeps_public_owner_absent_and_blocks_recovery(self) -> None:
        endpoint = AdbServerEndpoint("127.0.0.1", 5037)
        launcher = _Launcher(endpoint)
        owner = _ProcessAdbServerOwner(launcher)
        server = owner.acquire()
        launcher.handles[-1].fail_close = True
        bus = _RecordingBus()
        supervisor = AdbServerSupervisor(
            server,
            bus,
            _Scheduler(),
            AdbServerSupervisionPolicy(),
            _owner_manager=owner,
        )
        supervisor.start(recovery_enabled=True)

        supervisor.reconcile(AdbServerConnectionFailure("connection lost"))
        self.assertTrue(bus.close_unproven.wait(1.0))

        self.assertIsNone(supervisor.server)
        self.assertIsNone(owner.active_owner)
        self.assertEqual(len(launcher.handles), 1)
        supervisor.close()

    def test_stale_reconciliation_request_cannot_retire_new_generation(self) -> None:
        endpoint = AdbServerEndpoint("127.0.0.1", 5037)
        launcher = _Launcher(endpoint)
        owner = _ProcessAdbServerOwner(launcher)
        old = owner.acquire()
        owner.close(old)
        current = owner.acquire()
        bus = _RecordingBus()
        supervisor = AdbServerSupervisor(
            current,
            bus,
            _Scheduler(),
            AdbServerSupervisionPolicy(),
            _owner_manager=owner,
        )
        supervisor.start(recovery_enabled=False)

        bus.publish(
            AdbServerReconciliationRequested(
                endpoint,
                old.generation,
                AdbServerConnectionFailure("late failure"),
            )
        )

        self.assertIs(supervisor.server, current)
        self.assertIs(owner.active_owner, current)
        supervisor.close()


if __name__ == "__main__":
    unittest.main()
