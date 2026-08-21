from __future__ import annotations

import unittest

from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle.native import AdbServerCloseError
from adb.server.ownership import (
    AdbServerOwnershipLostError,
    _ProcessAdbServerOwner,
)


class _FakeHandle:
    def __init__(self, endpoint: AdbServerEndpoint) -> None:
        self._endpoint = endpoint
        self.close_calls = 0
        self.fail_close = False
        self._active = True

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self._endpoint

    @property
    def active(self) -> bool:
        return self._active

    def close(self) -> None:
        self.close_calls += 1
        if self.fail_close:
            raise AdbServerCloseError("termination not proven")
        self._active = False


class _FakeLauncher:
    def __init__(self, endpoint: AdbServerEndpoint) -> None:
        self.endpoint = endpoint
        self.handles: list[_FakeHandle] = []

    def launch(self) -> _FakeHandle:
        handle = _FakeHandle(self.endpoint)
        self.handles.append(handle)
        return handle


class ProcessOwnedServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.endpoint = AdbServerEndpoint("127.0.0.1", 5037)
        self.launcher = _FakeLauncher(self.endpoint)
        self.owner = _ProcessAdbServerOwner(self.launcher)

    def test_public_owner_exposes_only_identity_not_native_lifecycle(self) -> None:
        server = self.owner.acquire()

        self.assertEqual(server.endpoint, self.endpoint)
        self.assertEqual(server.generation, 1)
        self.assertFalse(hasattr(server, "active"))
        self.assertFalse(hasattr(server, "_native_handle"))

    def test_retirement_withdraws_public_owner_before_native_close(self) -> None:
        server = self.owner.acquire()
        handle = self.launcher.handles[-1]

        self.assertTrue(self.owner.retire(server))
        self.assertIsNone(self.owner.active_owner)
        self.assertEqual(handle.close_calls, 0)

        self.owner.dispose_retired(server)
        self.assertEqual(handle.close_calls, 1)
        self.assertIsNone(self.owner.active_owner)

    def test_unproven_close_never_resurrects_or_allows_next_generation(self) -> None:
        server = self.owner.acquire()
        handle = self.launcher.handles[-1]
        handle.fail_close = True

        self.owner.retire(server)
        with self.assertRaises(AdbServerCloseError):
            self.owner.dispose_retired(server)

        self.assertIsNone(self.owner.active_owner)
        with self.assertRaises(AdbServerOwnershipLostError):
            self.owner.acquire()
        self.assertEqual(len(self.launcher.handles), 1)

        handle.fail_close = False
        self.owner.dispose_retired(server)
        replacement = self.owner.acquire()
        self.assertEqual(replacement.generation, 2)
        self.assertIsNot(replacement, server)


if __name__ == "__main__":
    unittest.main()
