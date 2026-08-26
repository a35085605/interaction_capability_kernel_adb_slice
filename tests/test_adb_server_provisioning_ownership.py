from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from adb.bootstrap import AdbRuntimeBootstrap
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer, ServerEpoch, ServerEpochSequence
from adb.server.lifecycle.control.controller import AdbServerController
from adb.server.lifecycle.supervision.policy import AdbServerSupervisionPolicy
from adb.server.lifecycle.supervision.supervisor import AdbServerSupervisor
from eventing import EventSubscriptionToken
from scheduling import CalendarSchedule, MisfirePolicy, ScheduleToken


class _RecordingBackend:
    def __init__(self, fallback_endpoints: list[AdbServerEndpoint]) -> None:
        self._fallback_endpoints = list(fallback_endpoints)
        self._active: AdbServerEndpoint | None = None
        self.acquire_requests: list[AdbServerEndpoint | None] = []
        self.release_requests: list[AdbServerEndpoint] = []

    def acquire(self, endpoint: AdbServerEndpoint | None = None) -> AdbServerEndpoint:
        if self._active is not None:
            raise RuntimeError("backend already has an active endpoint")
        self.acquire_requests.append(endpoint)
        if endpoint is None:
            if not self._fallback_endpoints:
                raise RuntimeError("no fallback endpoint configured")
            endpoint = self._fallback_endpoints.pop(0)
        self._active = endpoint
        return endpoint

    def release(self, endpoint: AdbServerEndpoint) -> None:
        if endpoint != self._active:
            raise RuntimeError("release does not match active endpoint")
        self.release_requests.append(endpoint)
        self._active = None


class _EventBus:
    def __init__(self) -> None:
        self.events: list[object] = []

    def publish(self, event: object) -> None:
        self.events.append(event)

    def subscribe(self, event_type, handler) -> EventSubscriptionToken:
        return EventSubscriptionToken("subscription")

    def unsubscribe(self, token: EventSubscriptionToken) -> bool:
        return True


class _Scheduler:
    def schedule_at(
        self,
        deadline: datetime,
        event: object,
        *,
        misfire_policy: MisfirePolicy = MisfirePolicy.FIRE_ONCE,
    ) -> ScheduleToken:
        return ScheduleToken("at")

    def schedule_after(self, delay: timedelta, event: object) -> ScheduleToken:
        return ScheduleToken("after")

    def schedule_recurring(
        self,
        schedule: CalendarSchedule,
        event: object,
        *,
        misfire_policy: MisfirePolicy = MisfirePolicy.FIRE_ONCE,
    ) -> ScheduleToken:
        return ScheduleToken("recurring")

    def cancel(self, token: ScheduleToken) -> bool:
        return True


class AdbServerProvisioningOwnershipTests(unittest.TestCase):
    def test_controller_receives_endpoint_per_provision_request(self) -> None:
        first = AdbServerEndpoint("127.0.0.1", 5037)
        fallback = AdbServerEndpoint("127.0.0.1", 5038)
        backend = _RecordingBackend([fallback])
        controller = AdbServerController(backend, ServerEpochSequence())

        first_server = controller.provision(first)
        controller.retire(first_server)
        second_server = controller.provision(None)

        self.assertEqual(backend.acquire_requests, [first, None])
        self.assertEqual(second_server.endpoint, fallback)

    def test_runtime_pins_resolved_endpoint_for_successive_provisioning(self) -> None:
        resolved = AdbServerEndpoint("127.0.0.1", 5037)
        backend = _RecordingBackend([resolved])
        runtime = AdbRuntimeBootstrap(
            server_backend_factory=lambda: backend,
            pin_endpoint=True,
        ).build_minimal()

        initial = runtime.server
        self.assertIsNotNone(initial)
        assert initial is not None
        self.assertEqual(runtime.required_endpoint, resolved)

        runtime._retire_server(initial)
        successor = runtime._provision_server()

        self.assertEqual(backend.acquire_requests, [None, resolved])
        self.assertEqual(successor.endpoint, resolved)

    def test_runtime_can_request_fresh_endpoint_when_not_pinned(self) -> None:
        first = AdbServerEndpoint("127.0.0.1", 5037)
        second = AdbServerEndpoint("127.0.0.1", 5038)
        backend = _RecordingBackend([first, second])
        runtime = AdbRuntimeBootstrap(
            server_backend_factory=lambda: backend,
            pin_endpoint=False,
        ).build_minimal()

        initial = runtime.server
        self.assertIsNotNone(initial)
        assert initial is not None
        self.assertIsNone(runtime.required_endpoint)

        runtime._retire_server(initial)
        successor = runtime._provision_server()

        self.assertEqual(backend.acquire_requests, [None, None])
        self.assertEqual(successor.endpoint, second)

    def test_supervisor_depends_on_lifecycle_callbacks(self) -> None:
        initial = AdbServer(
            AdbServerEndpoint("127.0.0.1", 5037),
            ServerEpoch(1),
        )
        successor = AdbServer(
            AdbServerEndpoint("127.0.0.1", 5038),
            ServerEpoch(2),
        )
        retired: list[AdbServer] = []
        event_bus = _EventBus()
        supervisor = AdbServerSupervisor(
            initial,
            provision_server=lambda: successor,
            retire_server=retired.append,
            event_bus=event_bus,
            scheduler=_Scheduler(),
            policy=AdbServerSupervisionPolicy(),
            recovery_enabled=True,
        )

        self.assertIs(supervisor._provision_server(), successor)
        supervisor._dispose_retired_server(initial)

        self.assertEqual(retired, [initial])
        self.assertEqual(len(event_bus.events), 1)


if __name__ == "__main__":
    unittest.main()
