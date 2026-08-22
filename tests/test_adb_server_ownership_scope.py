from __future__ import annotations

import unittest
from unittest.mock import patch

from adb.server.control import SubprocessAdbServerController
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import _AdbServerSequence
from adb.server.ownership import (
    ANY_ADB_SERVER_TERMINATION_POLICY,
    AdbServerOwnership,
    OWNED_ONLY_ADB_SERVER_TERMINATION_POLICY,
    _AdbServerLifetimeStore,
)


class _FakeStatusReader:
    def read(self, endpoint: AdbServerEndpoint) -> object:
        return object()


class _FakeProcessLifetime:
    def __init__(self, endpoint: AdbServerEndpoint) -> None:
        self._endpoint = endpoint
        self.closed = False

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self._endpoint

    @property
    def active(self) -> bool:
        return not self.closed

    def close(self) -> None:
        self.closed = True


class _FakeLauncher:
    def __init__(self, lifetime: _FakeProcessLifetime) -> None:
        self.lifetime = lifetime

    def launch(self, endpoint: AdbServerEndpoint | None = None) -> _FakeProcessLifetime:
        return self.lifetime


class AdbServerOwnershipScopeTests(unittest.TestCase):
    def test_owned_only_policy_is_authorization_not_capability(self) -> None:
        self.assertTrue(
            OWNED_ONLY_ADB_SERVER_TERMINATION_POLICY.allows(AdbServerOwnership.OWNED)
        )
        self.assertFalse(
            OWNED_ONLY_ADB_SERVER_TERMINATION_POLICY.allows(AdbServerOwnership.ADOPTED)
        )
        self.assertFalse(
            OWNED_ONLY_ADB_SERVER_TERMINATION_POLICY.allows(AdbServerOwnership.UNKNOWN)
        )
        for ownership in AdbServerOwnership:
            self.assertTrue(ANY_ADB_SERVER_TERMINATION_POLICY.allows(ownership))

    def test_store_keeps_adb_record_while_backend_keeps_process_lifetime(self) -> None:
        endpoint = AdbServerEndpoint("127.0.0.1", 5038)
        lifetime = _FakeProcessLifetime(endpoint)
        store = _AdbServerLifetimeStore(_FakeLauncher(lifetime))
        sequence = _AdbServerSequence()

        server = store.acquire(endpoint, server_factory=sequence.next)

        self.assertEqual(store.active_server, server)
        self.assertIs(store.active_ownership, AdbServerOwnership.OWNED)
        self.assertEqual(store.ownership_of(server), AdbServerOwnership.OWNED)
        self.assertEqual(set(store._active_record.__slots__), {"server", "ownership"})
        self.assertNotIn("native", store._active_record.__slots__)

        self.assertTrue(store.retire(server))
        store.dispose_retired(server)
        self.assertTrue(lifetime.closed)

    def test_raw_controller_close_remains_independent_of_ownership_policy(self) -> None:
        controller = SubprocessAdbServerController(
            endpoint=AdbServerEndpoint("127.0.0.1", 5037),
            _status_reader=_FakeStatusReader(),
        )
        sentinel = object()
        with patch("adb._internal.subprocess.run_adb", return_value=sentinel) as run_adb:
            result = controller.close()

        self.assertIs(result, sentinel)
        run_adb.assert_called_once()
        args = run_adb.call_args.args[2]
        self.assertEqual(args[-1], "kill-server")


if __name__ == "__main__":
    unittest.main()
