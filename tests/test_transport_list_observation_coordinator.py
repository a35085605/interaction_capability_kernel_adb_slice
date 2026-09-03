from __future__ import annotations

import unittest

from adb.runtime.bootstrap import AdbRuntimeBootstrap
from adb.server.candidate import AdbServerCandidate
from adb.server.identity import AdbServerIdentityIssuer
from adb.server.lifecycle.backend import AdbServerBackendAcquireAchieved
from adb.server.state import AdbServerActivated, AdbServerStateStore
from adb.transport.model import AdbObservedTransportKind, AdbTransport
from adb.transport_list.coordinator import (
    AdbTransportListObservationCoordinator,
    AdbTransportListObservationServerConflict,
)
from adb.transport_list.identity import AdbTransportListIdentityIssuer
from adb.transport_list.model import AdbTransportList
from adb.transport_list.state import (
    AdbTransportListObservationStateConflict,
    AdbTransportListObserved,
    AdbTransportListStateStatus,
    AdbTransportListStateStore,
)
from adb.transport_list.watch.publication import AdbTransportListStateBackedWatchPublisher
from adb.transport_list.watch.signal import (
    AdbTransportListWatchObservation,
    AdbTransportListWatchStarted,
)
from networking import TcpAddress


ENDPOINT = TcpAddress("127.0.0.1", 5037)


def transport_list(serial: str) -> AdbTransportList:
    return AdbTransportList(
        [
            AdbTransport(
                serial_text=serial,
                transport_kind=AdbObservedTransportKind.unspecified(),
            )
        ]
    )


def activate_server(
    store: AdbServerStateStore,
    issuer: AdbServerIdentityIssuer,
    endpoint: TcpAddress = ENDPOINT,
):
    server = issuer.issue()
    result = store.activate(
        AdbServerCandidate(server, endpoint),
        store.snapshot(),
    )
    assert isinstance(result, AdbServerActivated)
    return server


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[object] = []

    def publish(self, event: object) -> None:
        self.events.append(event)


class StubServerBackend:
    def __init__(self, endpoint: TcpAddress = ENDPOINT) -> None:
        self.endpoint = endpoint
        self.released: list[TcpAddress] = []

    def acquire(self, endpoint: TcpAddress | None = None):
        assert endpoint is None or endpoint == self.endpoint
        return AdbServerBackendAcquireAchieved(self.endpoint)

    def release(self, endpoint: TcpAddress) -> None:
        self.released.append(endpoint)


class StaticReader:
    def __init__(self, value: AdbTransportList) -> None:
        self.value = value
        self.endpoints: list[TcpAddress] = []

    def read(self, endpoint: TcpAddress) -> AdbTransportList:
        self.endpoints.append(endpoint)
        return self.value


class CoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server_state = AdbServerStateStore()
        self.server_issuer = AdbServerIdentityIssuer()
        self.server = activate_server(self.server_state, self.server_issuer)
        self.transport_list_state = AdbTransportListStateStore()
        self.coordinator = AdbTransportListObservationCoordinator(
            self.transport_list_state,
            self.server_state,
            AdbTransportListIdentityIssuer(),
        )

    def test_observe_commits_current_server_evidence(self) -> None:
        observed = self.coordinator.observe(self.server, transport_list("device-a"))

        self.assertIsInstance(observed, AdbTransportListObserved)
        self.assertEqual(self.transport_list_state.current, transport_list("device-a"))

    def test_observe_rejects_non_authoritative_server(self) -> None:
        successor = self.server_issuer.issue()

        result = self.coordinator.observe(successor, transport_list("device-b"))

        self.assertIsInstance(result, AdbTransportListObservationServerConflict)
        self.assertEqual(result.server, successor)
        self.assertEqual(result.current_server, self.server)
        self.assertIsNone(self.transport_list_state.current)

    def test_prepare_server_invalidates_previous_server_evidence(self) -> None:
        first = self.coordinator.observe(self.server, transport_list("device-a"))
        self.assertIsInstance(first, AdbTransportListObserved)
        deactivated = self.server_state.deactivate(self.server)
        self.assertTrue(deactivated)
        successor = activate_server(self.server_state, self.server_issuer)

        prepared = self.coordinator.prepare_server(successor)

        self.assertTrue(prepared)
        snapshot = self.transport_list_state.snapshot()
        self.assertEqual(snapshot.status, AdbTransportListStateStatus.INVALIDATED)
        self.assertEqual(snapshot.transport_list, transport_list("device-a"))
        self.assertIsNone(snapshot.current)

    def test_expected_state_fence_preserves_newer_observation(self) -> None:
        expected = self.transport_list_state.snapshot()
        newer = self.coordinator.observe(self.server, transport_list("watch-newer"))
        self.assertIsInstance(newer, AdbTransportListObserved)

        stale = self.coordinator.observe(
            self.server,
            transport_list("read-stale"),
            expected=expected,
        )

        self.assertIsInstance(stale, AdbTransportListObservationStateConflict)
        self.assertEqual(self.transport_list_state.current, transport_list("watch-newer"))

    def test_watch_publisher_delegates_commit_to_coordinator(self) -> None:
        downstream = RecordingPublisher()
        publisher = AdbTransportListStateBackedWatchPublisher(
            publisher=downstream,
            coordinator=self.coordinator,
        )
        started = AdbTransportListWatchStarted(self.server)
        observation = AdbTransportListWatchObservation(
            self.server,
            transport_list("device-a"),
        )

        publisher.publish(started)
        publisher.publish(observation)

        self.assertEqual(downstream.events, [started, observation])
        self.assertEqual(self.transport_list_state.current, observation.transport_list)

    def test_watch_publisher_legacy_constructor_remains_supported(self) -> None:
        downstream = RecordingPublisher()
        publisher = AdbTransportListStateBackedWatchPublisher(
            self.transport_list_state,
            self.server_state,
            AdbTransportListIdentityIssuer(),
            downstream,
        )

        publisher.publish(AdbTransportListWatchStarted(self.server))
        publisher.publish(
            AdbTransportListWatchObservation(
                self.server,
                transport_list("legacy-device"),
            )
        )

        self.assertEqual(self.transport_list_state.current, transport_list("legacy-device"))


class RuntimeRefreshTests(unittest.TestCase):
    def build_runtime(self):
        backend = StubServerBackend()
        runtime = AdbRuntimeBootstrap(
            server_backend_factory=lambda: backend,
            endpoint=ENDPOINT,
        ).build_minimal()
        return runtime, backend

    def test_refresh_transport_list_commits_read_evidence(self) -> None:
        runtime, _ = self.build_runtime()
        reader = StaticReader(transport_list("read-current"))
        try:
            result = runtime.refresh_transport_list(reader)

            self.assertIsInstance(result, AdbTransportListObserved)
            self.assertEqual(runtime.transport_list.current, transport_list("read-current"))
            self.assertEqual(reader.endpoints, [ENDPOINT])
        finally:
            runtime.close()
            runtime.retire_server()

    def test_refresh_does_not_overwrite_observation_committed_during_read(self) -> None:
        runtime, _ = self.build_runtime()
        server = runtime.server
        self.assertIsNotNone(server)
        assert server is not None
        newer = transport_list("watch-newer")
        stale = transport_list("read-stale")
        committed_during_read: list[object] = []

        class RacingReader:
            def read(self, endpoint: TcpAddress) -> AdbTransportList:
                committed_during_read.append(
                    runtime._transport_list_observation.observe(server, newer)
                )
                return stale

        try:
            result = runtime.refresh_transport_list(RacingReader())

            self.assertIsInstance(committed_during_read[0], AdbTransportListObserved)
            self.assertIsInstance(result, AdbTransportListObservationStateConflict)
            self.assertEqual(runtime.transport_list.current, newer)
        finally:
            runtime.close()
            runtime.retire_server()

    def test_refresh_returns_server_conflict_when_server_changes_during_read(self) -> None:
        runtime, _ = self.build_runtime()
        captured_server = runtime.server
        self.assertIsNotNone(captured_server)
        assert captured_server is not None
        switched: list[bool] = []

        class ServerSwitchingReader:
            def read(self, endpoint: TcpAddress) -> AdbTransportList:
                switched.append(runtime.retire_server())
                runtime.provision_server()
                return transport_list("old-server-read")

        try:
            result = runtime.refresh_transport_list(ServerSwitchingReader())

            self.assertEqual(switched, [True])
            self.assertIsInstance(result, AdbTransportListObservationServerConflict)
            self.assertEqual(result.server, captured_server)
            self.assertEqual(result.current_server, runtime.server)
            self.assertNotEqual(result.current_server, captured_server)
        finally:
            runtime.close()
            runtime.retire_server()


if __name__ == "__main__":
    unittest.main()
