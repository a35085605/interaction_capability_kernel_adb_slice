from __future__ import annotations

import importlib.util
import unittest

import adb
import adb.transport_list as transport_list
import adb.transport_list.watch as watch


class AdbLegacyLifecycleCleanupTests(unittest.TestCase):
    def test_legacy_lifecycle_package_is_removed(self) -> None:
        self.assertIsNone(importlib.util.find_spec("adb._lifecycle"))

    def test_migration_scaffold_modules_are_removed(self) -> None:
        removed_modules = (
            "adb.aosp.errors",
            "adb.server.access",
            "adb.server.template",
            "adb.transport_list.watch.template",
        )

        for module_name in removed_modules:
            with self.subTest(module_name=module_name):
                self.assertIsNone(importlib.util.find_spec(module_name))

    def test_root_package_does_not_reexport_legacy_lifecycle_api(self) -> None:
        legacy_names = (
            "AcquireAccessMismatch",
            "AcquireBlocked",
            "AcquireCommitted",
            "AcquireExisting",
            "AcquireFailed",
            "AcquireSuperseded",
            "GenerationMismatch",
            "GLOBAL_RESOURCE_POOL",
            "ReleaseAccessDetached",
            "ReleaseAccessMismatch",
            "ReleaseAcquisitionRevoked",
            "ReleaseInactive",
            "ResourceEntry",
            "ResourcePool",
            "ResourceScope",
            "Snapshot",
        )

        for name in legacy_names:
            with self.subTest(name=name):
                self.assertFalse(hasattr(adb, name))
                self.assertNotIn(name, adb.__all__)

    def test_watch_packages_expose_only_request_and_result_terminology(self) -> None:
        removed_names = (
            "AdbTransportListWatchAccess",
            "AdbTransportListWatchAcquireOutcome",
            "AdbTransportListWatchReleaseOutcome",
        )

        for module in (watch, transport_list):
            for name in removed_names:
                with self.subTest(module=module.__name__, name=name):
                    self.assertFalse(hasattr(module, name))
                    self.assertNotIn(name, module.__all__)


if __name__ == "__main__":
    unittest.main()
