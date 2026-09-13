from __future__ import annotations

import socket
import unittest

from _lifecycle_new.capability.snapshot import LifecyclePhase
from adb.adapters.aosp.watch_lifecycle import SmartSocketAdbTransportListWatchLifecycle
from adb.transport_list.watch.access import AdbTransportListWatchAccess
from adb.transport_list.watch.error import AdbTransportListWatchError
from adb.transport_list.watch.generation import AdbTransportListWatchGenerationIssuer
from adb.transport_list.watch.lifecycle import (
    AdbTransportListWatchAcquireFailed,
    AdbTransportListWatchAcquireSucceeded,
    AdbTransportListWatchReleaseSucceeded,
)
from adb.transport_list.watch.request import AdbTransportListWatchRequest
from adb.transport_list.watch.template import AdbTransportListWatchAcquireError
from networking import TcpAddress


class _FakeSocket:
    def __init__(
        self,
        incoming: bytes,
        *,
        connect_error: OSError | None = None,
        close_failures: int = 0,
    ) -> None:
        self._incoming = bytearray(incoming)
        self._connect_error = connect_error
        self._close_failures = close_failures
        self.closed = False
        self.shutdown_calls = 0
        self.sent: list[bytes] = []
        self.timeouts: list[float | None] = []

    def settimeout(self, value: float | None) -> None:
        self.timeouts.append(value)

    def connect(self, address: object) -> None:
        if self._connect_error is not None:
            raise self._connect_error

    def sendall(self, payload: bytes) -> None:
        self.sent.append(payload)

    def recv(self, size: int) -> bytes:
        if not self._incoming:
            return b""
        chunk = bytes(self._incoming[:size])
        del self._incoming[:size]
        return chunk

    def shutdown(self, how: int) -> None:
        self.shutdown_calls += 1

    def close(self) -> None:
        if self._close_failures:
            self._close_failures -= 1
            raise OSError("close failed")
        self.closed = True


def _resolver(*args: object, **kwargs: object) -> list[tuple]:
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            0,
            "",
            ("127.0.0.1", 5037),
        )
    ]


class AdbTransportListWatchLifecycleTests(unittest.TestCase):
    def request(self) -> AdbTransportListWatchRequest:
        return AdbTransportListWatchRequest(TcpAddress("127.0.0.1", 5037))

    def lifecycle(self, sock: _FakeSocket) -> SmartSocketAdbTransportListWatchLifecycle:
        return SmartSocketAdbTransportListWatchLifecycle(
            AdbTransportListWatchGenerationIssuer(),
            _resolver=_resolver,
            _socket_factory=lambda *args: sock,
        )

    def test_access_name_is_compatibility_alias_for_request(self) -> None:
        self.assertIs(AdbTransportListWatchAccess, AdbTransportListWatchRequest)

    def test_session_is_physical_resource_and_public_capability_hides_cleanup(self) -> None:
        sock = _FakeSocket(b"OKAY0000")
        lifecycle = self.lifecycle(sock)
        idle = lifecycle.read()
        request = self.request()

        acquired = lifecycle.acquire(idle.generation, request)

        self.assertIsInstance(acquired, AdbTransportListWatchAcquireSucceeded)
        self.assertEqual(acquired.snapshot.phase, LifecyclePhase.ACTIVE)
        self.assertIs(acquired.snapshot.request, request)
        self.assertEqual(len(acquired.snapshot.capability.initial), 0)
        self.assertFalse(hasattr(acquired.snapshot.capability, "close"))
        self.assertFalse(sock.closed)

        released = lifecycle.release(idle.generation, request)

        self.assertIsInstance(released, AdbTransportListWatchReleaseSucceeded)
        self.assertTrue(sock.closed)
        self.assertEqual(lifecycle.read().phase, LifecyclePhase.IDLE)
        self.assertEqual(lifecycle.read().generation, released.next_generation)

    def test_failed_socket_cleanup_is_retained_until_explicit_release(self) -> None:
        sock = _FakeSocket(
            b"",
            connect_error=OSError("connect failed"),
            close_failures=1,
        )
        lifecycle = self.lifecycle(sock)
        idle = lifecycle.read()
        request = self.request()

        failed = lifecycle.acquire(idle.generation, request)

        self.assertIsInstance(failed, AdbTransportListWatchAcquireFailed)
        self.assertEqual(failed.snapshot.phase, LifecyclePhase.RELEASE_REQUIRED)
        self.assertIsInstance(failed.snapshot.last_error, AdbTransportListWatchAcquireError)
        self.assertFalse(sock.closed)

        released = lifecycle.release(idle.generation, request)

        self.assertIsInstance(released, AdbTransportListWatchReleaseSucceeded)
        self.assertTrue(sock.closed)
        self.assertEqual(lifecycle.read().phase, LifecyclePhase.IDLE)

    def test_data_plane_failure_does_not_mutate_lifecycle_ownership(self) -> None:
        sock = _FakeSocket(b"OKAY0000")
        lifecycle = self.lifecycle(sock)
        idle = lifecycle.read()
        request = self.request()
        acquired = lifecycle.acquire(idle.generation, request)
        self.assertIsInstance(acquired, AdbTransportListWatchAcquireSucceeded)

        with self.assertRaises(AdbTransportListWatchError):
            next(acquired.snapshot.capability.updates())

        active = lifecycle.read()
        self.assertEqual(active.phase, LifecyclePhase.ACTIVE)
        self.assertIs(active.request, request)
        self.assertFalse(sock.closed)

        released = lifecycle.release(active.generation, request)
        self.assertIsInstance(released, AdbTransportListWatchReleaseSucceeded)
        self.assertTrue(sock.closed)


if __name__ == "__main__":
    unittest.main()
