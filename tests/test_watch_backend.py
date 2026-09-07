from __future__ import annotations

from collections import deque
from contextlib import ExitStack
import importlib
import socket
from threading import Event
import unittest
from unittest.mock import Mock, patch

from adb.adapters.aosp.track_devices import (
    SmartSocketAdbTransportListWatcher,
    to_transport_list,
)
from adb.adapters.aosp.watch_backend import SmartSocketAdbTransportListWatchBackend
from adb.aosp.model.track_devices import Device, Devices
from adb.aosp.protocol.smart_socket.framing import encode_service
from adb.aosp.protocol.smart_socket.services import TRACK_DEVICES_PROTO_BINARY_SERVICE
from adb.transport_list.coordinator import AdbTransportListCoordinator
from adb.transport_list.model import AdbTransportList
from adb.transport_list.session_identity import AdbTransportListSessionIdentityIssuer
from adb.transport_list.state import AdbTransportListStateStore
from adb.transport_list.watch import (
    AdbTransportListWatchBackend,
    AdbTransportListWatchBackendFactory,
    AdbTransportListWatchError,
    AdbTransportListWatchProtocolFailure,
    AdbTransportListWatchServerConnectionFailure,
    AdbTransportListWatchServiceFailure,
    AdbTransportListWatchSession,
    AdbTransportListWatchStartSucceeded,
    ThreadedAdbTransportListWatchController,
    bind_transport_list_watch_session,
)
from eventing import EventPublisher
from networking import TcpAddress


ENDPOINT = TcpAddress("adb.example", 5037)
ADDRESS = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 5037))


def frame(payload: bytes) -> bytes:
    return f"{len(payload):04x}".encode("ascii") + payload


def device_payload(serial: str) -> bytes:
    # Devices.devices (field 1), containing Device.serial (field 1).
    encoded = serial.encode("utf-8")
    device = b"\x0a" + bytes([len(encoded)]) + encoded
    return b"\x0a" + bytes([len(device)]) + device


def expected_list(serial: str) -> AdbTransportList:
    return to_transport_list(Devices((Device(serial=serial),)))


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ScriptedSocket:
    """A deterministic socket that exposes framing, timeout, and cleanup behavior."""

    def __init__(self, *incoming: bytes | BaseException, chunk_size: int = 65536) -> None:
        self.incoming = deque(incoming)
        self.chunk_size = chunk_size
        self.timeouts: list[float | None] = []
        self.sent: list[bytes] = []
        self.connected: list[object] = []
        self.recv_calls = 0
        self.close_calls = 0
        self.shutdown_calls = 0
        self.connect_error: BaseException | None = None
        self.send_error: BaseException | None = None
        self.timeout_error: BaseException | None = None
        self.watch_mode_error: BaseException | None = None
        self.close_error: BaseException | None = None
        self.on_connect = lambda: None
        self.on_send = lambda: None
        self.on_recv = lambda: None

    def settimeout(self, value: float | None) -> None:
        self.timeouts.append(value)
        error = self.watch_mode_error if value is None else self.timeout_error
        if error is not None:
            raise error

    def connect(self, address: object) -> None:
        self.connected.append(address)
        self.on_connect()
        if self.connect_error is not None:
            raise self.connect_error

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)
        self.on_send()
        if self.send_error is not None:
            raise self.send_error

    def recv(self, size: int) -> bytes:
        if self.close_calls:
            raise AssertionError("read from a closed socket")
        self.recv_calls += 1
        self.on_recv()
        if not self.incoming:
            return b""
        item = self.incoming.popleft()
        if isinstance(item, BaseException):
            raise item
        count = min(size, self.chunk_size)
        if len(item) > count:
            self.incoming.appendleft(item[count:])
        return item[:count]

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error

    def shutdown(self, how: int) -> None:
        self.shutdown_calls += 1


class RecordingIssuer(AdbTransportListSessionIdentityIssuer):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.before_issue = lambda: None
        self.error: BaseException | None = None

    def issue(self):
        self.calls += 1
        self.before_issue()
        if self.error is not None:
            raise self.error
        return super().issue()


class WatchBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.issuer = RecordingIssuer()

    def backend(self, *sockets: ScriptedSocket):
        self.resolver = Mock(return_value=[ADDRESS])
        self.socket_factory = Mock(side_effect=sockets)
        return SmartSocketAdbTransportListWatchBackend(
            self.issuer,
            _resolver=self.resolver,
            _socket_factory=self.socket_factory,
            _clock=self.clock,
        )

    def test_open_returns_complete_session_and_one_ordered_update_iterator(self) -> None:
        sock = ScriptedSocket(
            b"OKAY" + frame(device_payload("first"))
            + frame(device_payload("second")) + frame(b""),
            chunk_size=1,
        )
        backend = self.backend(sock)

        def verify_ready():
            self.assertIsNone(sock.timeouts[-1])
            self.assertEqual(b"".join(sock.incoming), frame(device_payload("second")) + frame(b""))

        self.issuer.before_issue = verify_ready
        self.assertIsInstance(backend, AdbTransportListWatchBackend)
        session = backend.open(ENDPOINT)
        self.addCleanup(session.close)
        self.assertIsInstance(session, AdbTransportListWatchSession)
        self.assertEqual(session.initial, expected_list("first"))
        identity = session.session_identity
        updates = session.updates()
        self.assertIs(updates, session.updates())
        self.assertEqual(next(updates), expected_list("second"))
        self.assertEqual(next(session.updates()), AdbTransportList())
        self.assertIs(session.session_identity, identity)
        self.assertEqual(self.issuer.calls, 1)
        self.assertEqual(sock.sent, [encode_service(TRACK_DEVICES_PROTO_BINARY_SERVICE)])
        self.resolver.assert_called_once_with(ENDPOINT.host, ENDPOINT.port, type=socket.SOCK_STREAM)
        with self.assertRaises(AttributeError):
            session.session_identity = identity
        with self.assertRaises(AttributeError):
            session.initial = AdbTransportList()

    def test_empty_initial_is_success_and_close_prevents_any_read(self) -> None:
        sock = ScriptedSocket(b"OKAY0000", frame(device_payload("unused")))
        session = self.backend(sock).open(ENDPOINT)
        self.assertEqual(session.initial, AdbTransportList())
        reads = sock.recv_calls
        session.close()
        session.close()
        self.assertEqual(list(session.updates()), [])
        self.assertEqual(sock.recv_calls, reads)
        self.assertEqual(sock.close_calls, 1)

    def test_duplicate_open_is_rejected_without_io_or_identity_issuance(self) -> None:
        sock = ScriptedSocket(b"OKAY0000", frame(device_payload("still-active")))
        backend = self.backend(sock)
        session = backend.open(ENDPOINT)
        self.addCleanup(session.close)
        identity = session.session_identity
        with self.assertRaises(RuntimeError):
            backend.open(TcpAddress("another.example", 5038))
        self.assertEqual(self.socket_factory.call_count, 1)
        self.assertEqual(self.resolver.call_count, 1)
        self.assertEqual(self.issuer.calls, 1)
        self.assertIs(session.session_identity, identity)
        self.assertEqual(next(session.updates()), expected_list("still-active"))
        self.assertEqual(sock.close_calls, 0)

    def test_close_then_reopen_has_new_identity_and_stale_close_is_harmless(self) -> None:
        first = ScriptedSocket(b"OKAY0000", frame(b""))
        second = ScriptedSocket(b"OKAY0000", frame(device_payload("new")))
        backend = self.backend(first, second)
        old = backend.open(ENDPOINT)
        old_updates = old.updates()
        next(old_updates)
        old.close()
        new = backend.open(ENDPOINT)
        self.addCleanup(new.close)
        self.assertIsNot(old.session_identity, new.session_identity)
        self.assertLess(old.session_identity.epoch.value, new.session_identity.epoch.value)
        old.close()
        self.assertEqual(list(old_updates), [])
        self.assertEqual(second.close_calls, 0)
        self.assertEqual(next(new.updates()), expected_list("new"))
        with self.assertRaises(RuntimeError):
            backend.open(ENDPOINT)

    def test_consumer_break_requires_explicit_session_close(self) -> None:
        sock = ScriptedSocket(b"OKAY0000", frame(b"") + frame(device_payload("later")))
        backend = self.backend(sock)
        session = backend.open(ENDPOINT)
        self.addCleanup(session.close)
        for _ in session.updates():
            break
        self.assertEqual(sock.close_calls, 0)
        with self.assertRaises(RuntimeError):
            backend.open(ENDPOINT)
        self.assertEqual(next(session.updates()), expected_list("later"))

    def test_open_failures_clean_up_and_allow_retry_without_issuing_identity(self) -> None:
        cases = [
            ("connect", ScriptedSocket(), AdbTransportListWatchServerConnectionFailure),
            ("settimeout", ScriptedSocket(), AdbTransportListWatchServerConnectionFailure),
            ("send", ScriptedSocket(), AdbTransportListWatchServerConnectionFailure),
            ("timeout", ScriptedSocket(socket.timeout("startup timed out")), AdbTransportListWatchServerConnectionFailure),
            ("eof", ScriptedSocket(b"OK"), AdbTransportListWatchServerConnectionFailure),
            ("service", ScriptedSocket(b"FAIL" + frame(b"unsupported")), AdbTransportListWatchServiceFailure),
            ("status", ScriptedSocket(b"NOPE"), AdbTransportListWatchProtocolFailure),
            ("failure length", ScriptedSocket(b"FAILzzzz"), AdbTransportListWatchProtocolFailure),
            ("failure utf8", ScriptedSocket(b"FAIL" + frame(b"\xff")), AdbTransportListWatchProtocolFailure),
            ("frame", ScriptedSocket(b"OKAYzzzz"), AdbTransportListWatchProtocolFailure),
            ("payload", ScriptedSocket(b"OKAY" + frame(b"\x0a\x05x")), AdbTransportListWatchProtocolFailure),
            ("watch mode", ScriptedSocket(b"OKAY0000"), AdbTransportListWatchServerConnectionFailure),
        ]
        cases[0][1].connect_error = OSError("refused")
        cases[1][1].timeout_error = OSError("cannot set timeout")
        cases[2][1].send_error = OSError("send failed")
        cases[-1][1].watch_mode_error = OSError("cannot enter watch mode")
        for name, failed, failure_type in cases:
            with self.subTest(stage=name):
                self.issuer = RecordingIssuer()
                backend = self.backend(failed, ScriptedSocket(b"OKAY0000"))
                with self.assertRaises(AdbTransportListWatchError) as caught:
                    backend.open(ENDPOINT)
                self.assertIsInstance(caught.exception.failure, failure_type)
                self.assertEqual(failed.close_calls, 1)
                self.assertEqual(self.issuer.calls, 0)
                backend.open(ENDPOINT).close()

    def test_resolution_and_socket_creation_failures_allow_retry(self) -> None:
        for stage in ("resolution", "socket creation", "no candidates"):
            with self.subTest(stage=stage):
                backend = self.backend(ScriptedSocket(b"OKAY0000"))
                if stage == "resolution":
                    self.resolver.side_effect = [socket.gaierror("unknown host"), [ADDRESS]]
                elif stage == "socket creation":
                    self.socket_factory.side_effect = [OSError("out of sockets"), ScriptedSocket(b"OKAY0000")]
                else:
                    self.resolver.side_effect = [[], [ADDRESS]]
                with self.assertRaises(AdbTransportListWatchError) as caught:
                    backend.open(ENDPOINT)
                self.assertIsInstance(caught.exception.failure, AdbTransportListWatchServerConnectionFailure)
                backend.open(ENDPOINT).close()

    def test_address_fallback_closes_failed_candidate_and_shares_deadline(self) -> None:
        failed = ScriptedSocket()
        failed.connect_error = OSError("first address refused")
        failed.on_connect = lambda: self.clock.advance(1)
        good = ScriptedSocket(b"OKAY0000")
        good.on_connect = lambda: self.clock.advance(1)
        good.on_send = lambda: self.clock.advance(1)
        backend = self.backend(failed, good)

        def resolve(*args, **kwargs):
            self.clock.advance(50)  # DNS does not consume the startup deadline.
            return [ADDRESS, ADDRESS]

        self.resolver.side_effect = resolve
        session = backend.open(ENDPOINT, startup_timeout_seconds=5)
        self.addCleanup(session.close)
        self.assertEqual(failed.close_calls, 1)
        self.assertEqual(failed.timeouts, [5.0])
        self.assertEqual(good.timeouts, [4.0, 3.0, 2.0, 2.0, None])

    def test_deadline_covers_connect_handshake_and_initial_frame(self) -> None:
        for stage in ("connect", "send", "initial header", "initial payload", "fallback"):
            with self.subTest(stage=stage):
                self.clock = FakeClock()
                self.issuer = RecordingIssuer()
                failed = ScriptedSocket(b"OKAY" + frame(device_payload("first")))
                if stage in ("connect", "fallback"):
                    failed.on_connect = lambda: self.clock.advance(5)
                elif stage == "send":
                    failed.on_send = lambda: self.clock.advance(5)
                else:
                    read_number = 1 if stage == "initial header" else 2

                    def expire_on_read():
                        if failed.recv_calls == read_number:
                            self.clock.advance(5)

                    failed.on_recv = expire_on_read
                backend = self.backend(failed, ScriptedSocket(b"OKAY0000"))
                if stage == "fallback":
                    failed.connect_error = OSError("refused")
                    unused = ScriptedSocket()
                    self.resolver.return_value = [ADDRESS, ADDRESS]
                    self.socket_factory.side_effect = [failed, unused, ScriptedSocket(b"OKAY0000")]
                with self.assertRaises(AdbTransportListWatchError) as caught:
                    backend.open(ENDPOINT, startup_timeout_seconds=5)
                self.assertIn("timed out", str(caught.exception))
                self.assertEqual(failed.close_calls, 1)
                self.assertEqual(self.issuer.calls, 0)
                if stage == "fallback":
                    self.assertEqual(unused.connected, [])
                    self.assertEqual(unused.close_calls, 1)
                    self.resolver.return_value = [ADDRESS]
                backend.open(ENDPOINT).close()

    def test_issuer_and_session_construction_failures_release_socket(self) -> None:
        for stage in ("issuer", "construction"):
            with self.subTest(stage=stage):
                first = ScriptedSocket(b"OKAY0000")
                backend = self.backend(first, ScriptedSocket(b"OKAY0000"))
                original = OSError("issuer failed") if stage == "issuer" else TypeError("cannot construct")
                with ExitStack() as stack:
                    if stage == "issuer":
                        self.issuer.error = original
                    else:
                        stack.enter_context(patch(
                            "adb.adapters.aosp.watch_backend._SmartSocketWatchSession",
                            side_effect=original,
                        ))
                    with self.assertRaises(type(original)) as caught:
                        backend.open(ENDPOINT)
                    self.assertIs(caught.exception, original)
                self.issuer.error = None
                self.assertEqual(first.close_calls, 1)
                backend.open(ENDPOINT).close()

    def test_unexpected_establishment_errors_are_preserved_and_cleaned_up(self) -> None:
        for stage in ("connect", "read"):
            with self.subTest(stage=stage):
                original = TypeError("unexpected adapter error")
                sock = ScriptedSocket(original)
                if stage == "connect":
                    sock.connect_error = original
                backend = self.backend(sock, ScriptedSocket(b"OKAY0000"))
                with self.assertRaises(TypeError) as caught:
                    backend.open(ENDPOINT)
                self.assertIs(caught.exception, original)
                self.assertEqual(sock.close_calls, 1)
                backend.open(ENDPOINT).close()

    def test_update_failures_are_terminal_close_once_and_allow_reopen(self) -> None:
        cases = [
            (b"", AdbTransportListWatchServerConnectionFailure),
            (OSError("read failed"), AdbTransportListWatchServerConnectionFailure),
            (b"zzzz", AdbTransportListWatchProtocolFailure),
            (frame(b"\x0a\x05x"), AdbTransportListWatchProtocolFailure),
        ]
        for incoming, failure_type in cases:
            with self.subTest(incoming=incoming):
                failed = ScriptedSocket(b"OKAY0000", incoming)
                backend = self.backend(failed, ScriptedSocket(b"OKAY0000"))
                session = backend.open(ENDPOINT)
                with self.assertRaises(AdbTransportListWatchError) as caught:
                    next(session.updates())
                self.assertIsInstance(caught.exception.failure, failure_type)
                session.close()
                reads = failed.recv_calls
                self.assertEqual(list(session.updates()), [])
                self.assertEqual(failed.recv_calls, reads)
                self.assertEqual(failed.close_calls, 1)
                replacement = backend.open(ENDPOINT)
                self.assertIsNot(replacement.session_identity, session.session_identity)
                replacement.close()

    def test_update_programming_error_is_preserved_and_session_is_closed(self) -> None:
        original = TypeError("unexpected decoder error")
        sock = ScriptedSocket(b"OKAY0000", original)
        backend = self.backend(sock, ScriptedSocket(b"OKAY0000"))
        session = backend.open(ENDPOINT)
        with self.assertRaises(TypeError) as caught:
            next(session.updates())
        self.assertIs(caught.exception, original)
        self.assertEqual(sock.close_calls, 1)
        backend.open(ENDPOINT).close()

    def test_cleanup_failure_does_not_replace_primary_failure(self) -> None:
        for stage in ("connect", "startup", "update", "issuer", "programming"):
            with self.subTest(stage=stage):
                original = TypeError("primary programming failure")
                if stage in ("update", "issuer"):
                    sock = ScriptedSocket(b"OKAY0000", b"zzzz")
                else:
                    sock = ScriptedSocket(original if stage == "programming" else b"NOPE")
                sock.close_error = OSError("secondary cleanup failure")
                if stage == "connect":
                    sock.connect_error = OSError("primary connect failure")
                if stage == "issuer":
                    self.issuer.error = original
                backend = self.backend(sock, ScriptedSocket(b"OKAY0000"))
                session = backend.open(ENDPOINT) if stage == "update" else None
                expected_error = TypeError if stage in ("programming", "issuer") else AdbTransportListWatchError
                with self.assertRaises(expected_error) as caught:
                    if session is None:
                        backend.open(ENDPOINT)
                    else:
                        next(session.updates())
                self.assertNotIn("secondary cleanup", str(caught.exception))
                self.assertEqual(sock.close_calls, 1)
                self.issuer.error = None
                backend.open(ENDPOINT).close()

    def test_explicit_close_error_is_reported_but_ownership_is_terminal(self) -> None:
        sock = ScriptedSocket(b"OKAY0000")
        sock.close_error = OSError("close failed")
        backend = self.backend(sock, ScriptedSocket(b"OKAY0000"))
        session = backend.open(ENDPOINT)
        with self.assertRaises(AdbTransportListWatchError) as caught:
            session.close()
        self.assertIsInstance(caught.exception.failure, AdbTransportListWatchServerConnectionFailure)
        session.close()
        self.assertEqual(sock.close_calls, 1)
        self.assertEqual(list(session.updates()), [])
        backend.open(ENDPOINT).close()

    def test_invalid_arguments_do_not_start_io_or_issue_identity(self) -> None:
        backend = self.backend()
        with self.assertRaises(TypeError):
            backend.open("adb.example")
        for value in (True, "5", None):
            with self.subTest(value=value), self.assertRaises(TypeError):
                backend.open(ENDPOINT, startup_timeout_seconds=value)
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                backend.open(ENDPOINT, startup_timeout_seconds=value)
        self.resolver.assert_not_called()
        self.socket_factory.assert_not_called()
        self.assertEqual(self.issuer.calls, 0)

    def test_factory_can_share_issuer_across_backend_lifetimes(self) -> None:
        def make_backend(identity_issuer):
            return SmartSocketAdbTransportListWatchBackend(
                identity_issuer,
                _resolver=Mock(return_value=[ADDRESS]),
                _socket_factory=Mock(return_value=ScriptedSocket(b"OKAY0000")),
                _clock=self.clock,
            )

        factory: AdbTransportListWatchBackendFactory = make_backend
        first = factory(self.issuer).open(ENDPOINT)
        first.close()
        second = factory(self.issuer).open(ENDPOINT)
        self.addCleanup(second.close)
        self.assertIsNot(first.session_identity, second.session_identity)
        self.assertLess(first.session_identity.epoch.value, second.session_identity.epoch.value)

    def test_new_path_does_not_use_legacy_binding_or_change_authority(self) -> None:
        publisher = Mock(spec=EventPublisher)
        state = AdbTransportListStateStore(self.issuer)
        state.open_session_admission()
        coordinator = AdbTransportListCoordinator(state, publisher=publisher)
        identity = coordinator.begin()
        coordinator.observe_initial(identity, expected_list("authoritative"))
        before = state.snapshot()
        publisher.reset_mock()
        backend = self.backend(ScriptedSocket(b"OKAY0000", frame(b"")))
        with ExitStack() as stack:
            for target in (
                "adb.adapters.aosp.track_devices.SmartSocketAdbTransportListWatcher.open",
                "adb.transport_list.watch.session.bind_transport_list_watch_session",
                "adb.transport_list.coordinator.AdbTransportListCoordinator.begin",
                "adb.transport_list.coordinator.AdbTransportListCoordinator.revoke",
            ):
                stack.enter_context(patch(target, side_effect=AssertionError("legacy/domain call")))
            session = backend.open(ENDPOINT)
            next(session.updates())
            session.close()
        self.assertIs(state.snapshot(), before)
        publisher.publish.assert_not_called()


class LegacyCompatibilityTests(unittest.TestCase):
    def test_runtime_and_supervision_imports_remain_available(self) -> None:
        for module in (
            "adb.runtime.bootstrap", "adb.runtime.core", "adb.runtime.managed",
            "adb.transport_list.watch.supervision.supervisor",
        ):
            with self.subTest(module=module):
                importlib.import_module(module)

    def test_legacy_smart_socket_watch_and_authority_binding_still_work(self) -> None:
        sock = ScriptedSocket(b"OKAY0000", frame(device_payload("legacy")))
        issuer = AdbTransportListSessionIdentityIssuer()
        state = AdbTransportListStateStore(issuer)
        state.open_session_admission()
        coordinator = AdbTransportListCoordinator(state)
        watcher = SmartSocketAdbTransportListWatcher(ENDPOINT)
        with patch("adb.adapters.aosp.track_devices.socket.getaddrinfo", return_value=[ADDRESS]), patch(
            "adb.adapters.aosp.track_devices.socket.socket", return_value=sock
        ):
            stream = watcher.open()
        self.assertIsNotNone(stream)
        session = bind_transport_list_watch_session(
            coordinator, stream, stream.initial, attachment=watcher
        )
        self.assertIsNotNone(session)
        self.addCleanup(session.close)
        coordinator.observe_initial(session.session_identity, session.initial)
        self.assertIs(state.session, session.session_identity)
        self.assertEqual(next(session.updates()), expected_list("legacy"))
        session.close()
        self.assertIsNone(state.session)
        self.assertEqual(sock.close_calls, 1)

    def test_existing_controller_starts_and_stops_with_legacy_attachment(self) -> None:
        released = Event()

        class IdleStream:
            initial = AdbTransportList()

            def updates(self):
                if not released.wait(2):
                    raise AssertionError("legacy controller did not close its stream")
                return
                yield  # Make this an iterator that finishes when close releases it.

            def close(self):
                released.set()

        class Attachment:
            address = ENDPOINT

            def open(self):
                return IdleStream()

            def close(self):
                released.set()

        state = AdbTransportListStateStore(AdbTransportListSessionIdentityIssuer())
        state.open_session_admission()
        controller = ThreadedAdbTransportListWatchController(
            ENDPOINT, Mock(spec=EventPublisher), AdbTransportListCoordinator(state),
            _attachment_factory=lambda endpoint, timeout: Attachment(),
        )
        self.addCleanup(controller.close)
        result = controller.start()
        self.assertIsInstance(result, AdbTransportListWatchStartSucceeded)
        self.assertIs(state.session, result.session_identity)
        self.assertEqual(state.current, AdbTransportList())
        controller.stop()
        self.assertFalse(controller.active)
        self.assertIsNone(state.session)


if __name__ == "__main__":
    unittest.main()
