from __future__ import annotations

import unittest

from adb.adapters.aosp.track_devices_session import AospTrackDevicesSessionDriver
from adb.aosp.io.track_devices import AospTrackDevicesSession
from lifecycle.resource.result import ResourceCleanupStatus


class CloseErrorSocket:
    def __init__(self, *, relinquish_on_error: bool) -> None:
        self.relinquish_on_error = relinquish_on_error
        self.relinquished = False

    def shutdown(self, how: int) -> None:
        return None

    def close(self) -> None:
        if self.relinquish_on_error:
            self.relinquished = True
        raise OSError("close diagnostic")

    def fileno(self) -> int:
        return -1 if self.relinquished else 42


class AdapterCleanupTests(unittest.TestCase):
    def test_aosp_session_close_error_after_relinquish_is_complete_with_diagnostic(self) -> None:
        session = AospTrackDevicesSession(
            CloseErrorSocket(relinquish_on_error=True)  # type: ignore[arg-type]
        )

        result = AospTrackDevicesSessionDriver().cleanup((session,))

        self.assertIs(result.status, ResourceCleanupStatus.COMPLETE)
        self.assertEqual(result.remaining_resources, ())
        self.assertEqual(len(result.errors), 1)
        self.assertTrue(session.closed)

    def test_aosp_session_close_error_with_live_descriptor_is_blocked(self) -> None:
        session = AospTrackDevicesSession(
            CloseErrorSocket(relinquish_on_error=False)  # type: ignore[arg-type]
        )

        result = AospTrackDevicesSessionDriver().cleanup((session,))

        self.assertIs(result.status, ResourceCleanupStatus.BLOCKED)
        self.assertEqual(result.remaining_resources, (session,))
        self.assertEqual(len(result.errors), 1)
        self.assertFalse(session.closed)


if __name__ == "__main__":
    unittest.main()
