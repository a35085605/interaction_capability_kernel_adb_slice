from __future__ import annotations

import unittest

from adb.transport_list.model import AdbTransportList
from adb.transport_list.store import AdbTransportListStateStore


class AdbTransportListStateStoreTests(unittest.TestCase):
    def test_equal_updates_are_idempotent_and_clear_advances_once(self) -> None:
        store = AdbTransportListStateStore()
        initial = store.read()
        self.assertIsNone(initial.transport_list)

        empty = AdbTransportList()
        updated = store.update(empty)
        self.assertEqual(updated.transport_list, empty)
        self.assertNotEqual(updated.generation, initial.generation)

        repeated = store.update(empty)
        self.assertEqual(repeated, updated)

        cleared = store.clear()
        self.assertIsNone(cleared.transport_list)
        self.assertNotEqual(cleared.generation, updated.generation)
        self.assertEqual(store.clear(), cleared)


if __name__ == "__main__":
    unittest.main()
