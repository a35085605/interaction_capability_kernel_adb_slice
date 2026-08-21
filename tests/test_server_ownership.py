from __future__ import annotations

import socket
import subprocess
import threading
import unittest

from adb.managed import AdbManagedRuntime
from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle.adapters import SubprocessAdbServerLauncher
from adb.server.lifecycle.native import AdbServerCloseError, AdbServerLaunchError
from adb.server.ownership import (
    AdbServerStaleOwnerError,
    _ProcessAdbServerOwner,
)


class _FakeNativeHandle:
    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        *,
        fail_close_calls: int = 0,
    ) -> None:
        self._endpoint = endpoint
        self.running = True
        self.close_calls = 0
        self.fail_close_calls = fail_close_calls

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self._endpoint

    @property
    def active(self) -> bool:
        return self.running

    def close(self) -> None:
        self.close_calls += 1
        if self.close_calls <= self.fail_close_calls:
            raise AdbServerCloseError("scripted close failure")
        self.running = False


class _FakeLauncher:
    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        *,
        fail_on_calls: frozenset[int] = frozenset(),
        fail_close_calls: int = 0,
    ) -> None:
        self.endpoint = endpoint
        self.fail_on_calls = fail_on_calls
        self.fail_close_calls = fail_close_calls
        self.calls = 0
        self.handles: list[_FakeNativeHandle] = []

    def launch(self) -> _FakeNativeHandle:
        self.calls += 1
        if self.calls in self.fail_on_calls:
            raise AdbServerLaunchError("scripted launch failure")
        handle = _FakeNativeHandle(
            self.endpoint,
            fail_close_calls=self.fail_close_calls if not self.handles else 0,
        )
        self.handles.append(handle)
        return handle


class _BlockingLauncher(_FakeLauncher):
    def __init__(self, endpoint: AdbServerEndpoint) -> None:
        super().__init__(endpoint)
        self.entered = threading.Event()
        self.release = threading.Event()

    def launch(self) -> _FakeNativeHandle:
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=2.0):
            raise RuntimeError("test launcher release was not signaled")
        handle = _FakeNativeHandle(self.endpoint)
        self.handles.append(handle)
        return handle


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = 0

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        if self.returncode is None:
            raise subprocess.TimeoutExpired("adb", timeout)
        return self.returncode


class _ReadyStatusReader:
    def __init__(self) -> None:
        self.endpoints: list[AdbServerEndpoint] = []

    def read(self, endpoint: AdbServerEndpoint) -> object:
        self.endpoints.append(endpoint)
        return object()


class ProcessAdbServerOwnerTests(unittest.TestCase):
    def test_acquire_returns_single_active_generation(self) -> None:
        endpoint = AdbServerEndpoint("127.0.0.1", 5040)
        launcher = _FakeLauncher(endpoint)
        manager = _ProcessAdbServerOwner(launcher)

        first = manager.acquire()
        second = manager.acquire()

        self.assertIs(first, second)
        self.assertEqual(first.generation, 1)
        self.assertEqual(first.endpoint, endpoint)
        self.assertEqual(launcher.calls, 1)
        self.assertIs(manager.active_owner, first)

    def test_concurrent_callers_launch_only_once(self) -> None:
        endpoint = AdbServerEndpoint("127.0.0.1", 5041)
        launcher = _BlockingLauncher(endpoint)
        manager = _ProcessAdbServerOwner(launcher)
        owners: list[object] = []
        errors: list[BaseException] = []

        def acquire() -> None:
            try:
                owners.append(manager.acquire())
            except BaseException as exc:  # pragma: no cover - assertion aid
                errors.append(exc)

        first_thread = threading.Thread(target=acquire)
        first_thread.start()
        self.assertTrue(launcher.entered.wait(timeout=1.0))
        second_thread = threading.Thread(target=acquire)
        second_thread.start()
        launcher.release.set()
        first_thread.join(timeout=2.0)
        second_thread.join(timeout=2.0)

        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(launcher.calls, 1)
        self.assertEqual(len(owners), 2)
        self.assertIs(owners[0], owners[1])

    def test_invalidate_closes_exact_native_handle_before_next_generation(self) -> None:
        endpoint = AdbServerEndpoint("127.0.0.1", 5042)
        launcher = _FakeLauncher(endpoint)
        manager = _ProcessAdbServerOwner(launcher)
        first = manager.acquire()
        first_handle = launcher.handles[0]

        self.assertTrue(manager.invalidate(first))

        self.assertFalse(first.active)
        self.assertFalse(first_handle.running)
        self.assertEqual(first_handle.close_calls, 1)
        self.assertIsNone(manager.active_owner)

        second = manager.acquire()
        self.assertIsNot(second, first)
        self.assertEqual(second.generation, 2)
        self.assertEqual(second.endpoint, endpoint)
        self.assertEqual(launcher.calls, 2)

    def test_repeated_invalidate_of_retired_owner_is_noop(self) -> None:
        launcher = _FakeLauncher(AdbServerEndpoint("127.0.0.1", 5043))
        manager = _ProcessAdbServerOwner(launcher)
        owner = manager.acquire()

        self.assertTrue(manager.invalidate(owner))
        self.assertFalse(manager.invalidate(owner))
        self.assertEqual(launcher.handles[0].close_calls, 1)

    def test_stale_close_cannot_touch_new_generation(self) -> None:
        launcher = _FakeLauncher(AdbServerEndpoint("127.0.0.1", 5044))
        manager = _ProcessAdbServerOwner(launcher)
        first = manager.acquire()
        manager.invalidate(first)
        second = manager.acquire()
        second_handle = launcher.handles[1]

        with self.assertRaises(AdbServerStaleOwnerError):
            manager.close(first)

        self.assertTrue(second.active)
        self.assertIs(manager.active_owner, second)
        self.assertEqual(second_handle.close_calls, 0)

    def test_close_failure_keeps_generation_fenced_until_exact_handle_closes(self) -> None:
        launcher = _FakeLauncher(
            AdbServerEndpoint("127.0.0.1", 5045),
            fail_close_calls=1,
        )
        manager = _ProcessAdbServerOwner(launcher)
        owner = manager.acquire()

        with self.assertRaises(AdbServerCloseError):
            manager.close(owner)

        self.assertFalse(owner.active)
        self.assertIsNone(manager.active_owner)
        manager.close(owner)
        replacement = manager.acquire()
        self.assertEqual(replacement.generation, 2)

    def test_launch_failure_leaves_no_owner_and_next_acquire_retries(self) -> None:
        launcher = _FakeLauncher(
            AdbServerEndpoint("127.0.0.1", 5046),
            fail_on_calls=frozenset({1}),
        )
        manager = _ProcessAdbServerOwner(launcher)

        with self.assertRaises(AdbServerLaunchError):
            manager.acquire()
        self.assertIsNone(manager.active_owner)

        owner = manager.acquire()
        self.assertEqual(owner.generation, 1)
        self.assertEqual(launcher.calls, 2)

    def test_managed_runtime_requires_active_owned_generation(self) -> None:
        launcher = _FakeLauncher(AdbServerEndpoint("127.0.0.1", 5047))
        manager = _ProcessAdbServerOwner(launcher)
        owner = manager.acquire()

        runtime = AdbManagedRuntime(owner)
        self.assertIs(runtime.server, owner)
        self.assertEqual(runtime.endpoint, owner.endpoint)

        manager.invalidate(owner)
        with self.assertRaises(ValueError):
            AdbManagedRuntime(owner)
        with self.assertRaises(TypeError):
            AdbManagedRuntime(owner.endpoint)  # type: ignore[arg-type]


class SubprocessAdbServerLauncherTests(unittest.TestCase):
    def test_existing_listener_is_never_adopted(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        host, port = listener.getsockname()[:2]
        endpoint = AdbServerEndpoint(str(host), int(port))
        popen_called = False

        def unexpected_popen(*args, **kwargs):
            nonlocal popen_called
            popen_called = True
            raise AssertionError("occupied endpoint must fail before child launch")

        launcher = SubprocessAdbServerLauncher(
            endpoint,
            _popen_factory=unexpected_popen,
            _status_reader=_ReadyStatusReader(),
            _socket_activation_supported=True,
        )

        with self.assertRaises(AdbServerLaunchError):
            launcher.launch()
        self.assertFalse(popen_called)

    def test_launcher_passes_owned_listening_fd_to_foreground_adb_child(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []
        processes: list[_FakeProcess] = []
        reader = _ReadyStatusReader()

        def fake_popen(args, **kwargs):
            process = _FakeProcess()
            processes.append(process)
            calls.append((list(args), dict(kwargs)))
            return process

        launcher = SubprocessAdbServerLauncher(
            _popen_factory=fake_popen,
            _status_reader=reader,
            _socket_activation_supported=True,
        )

        first = launcher.launch()
        first_endpoint = first.endpoint
        first.close()
        second = launcher.launch()

        self.assertEqual(second.endpoint, first_endpoint)
        self.assertEqual(launcher.endpoint, first_endpoint)
        self.assertEqual(len(calls), 2)
        command, kwargs = calls[0]
        self.assertEqual(command[:3], ["adb", "server", "nodaemon"])
        self.assertEqual(command[3], "-L")
        self.assertTrue(command[4].startswith("acceptfd:"))
        self.assertNotIn("start-server", command)
        self.assertNotIn("kill-server", command)
        self.assertEqual(len(kwargs["pass_fds"]), 1)  # type: ignore[arg-type]
        self.assertEqual(reader.endpoints[0], first_endpoint)
        self.assertEqual(processes[0].terminate_calls, 1)
        second.close()

    def test_platform_without_socket_activation_fails_without_adopting(self) -> None:
        launcher = SubprocessAdbServerLauncher(
            _status_reader=_ReadyStatusReader(),
            _socket_activation_supported=False,
        )

        with self.assertRaisesRegex(AdbServerLaunchError, "platform-specific"):
            launcher.launch()


if __name__ == "__main__":
    unittest.main()
