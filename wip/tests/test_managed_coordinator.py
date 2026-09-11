from __future__ import annotations

import threading
import unittest
from dataclasses import dataclass

from adb._managed import (
    AcquireAccessMismatch,
    AcquireBusy,
    AcquireCommitted,
    AcquireExisting,
    AcquireSuperseded,
    Adapter,
    ManagedCoordinator,
    ReleaseAcquisitionRevoked,
    ReleaseDetached,
)
from adb._resource import ResourcePool


@dataclass(frozen=True)
class Access:
    name: str


@dataclass
class ResourceSet:
    name: str
    cleaned: bool = False


class AccessModel:
    def requirements(self, access: Access) -> str:
        return access.name


class Projection:
    def project(self, access: Access, resources: ResourceSet) -> tuple[str, ResourceSet]:
        return access.name, resources


class Lifecycle:
    def __init__(self) -> None:
        self.acquired: list[ResourceSet] = []
        self.cleaned: list[ResourceSet] = []
        self.fail_cleanup = False

    def acquire(self, requirements: str, acquisition) -> ResourceSet:
        resources = ResourceSet(requirements)
        self.acquired.append(resources)
        return resources

    def cleanup(self, resources: ResourceSet) -> None:
        if self.fail_cleanup:
            raise RuntimeError("cleanup failed")
        resources.cleaned = True
        self.cleaned.append(resources)


class BlockingLifecycle(Lifecycle):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.allow_return = threading.Event()
        self.saw_cancellation = False

    def acquire(self, requirements: str, acquisition) -> ResourceSet:
        self.started.set()
        self.allow_return.wait(timeout=5)
        self.saw_cancellation = acquisition.cancellation.is_set()
        resources = ResourceSet(requirements)
        self.acquired.append(resources)
        return resources


class Generations:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> int:
        self.value += 1
        return self.value


class ManagedCoordinatorTests(unittest.TestCase):
    def make_coordinator(self, lifecycle: Lifecycle):
        pool: ResourcePool[Access, ResourceSet] = ResourcePool()
        adapter = Adapter(AccessModel(), lifecycle, Projection())
        coordinator = ManagedCoordinator(Generations(), adapter, resource_pool=pool)
        return coordinator, pool

    def test_acquire_commit_existing_mismatch_and_release(self) -> None:
        lifecycle = Lifecycle()
        coordinator, pool = self.make_coordinator(lifecycle)
        access = Access("server")
        other = Access("watch")

        committed = coordinator.acquire(1, access)
        self.assertIsInstance(committed, AcquireCommitted)
        resources = pool.lookup(access)
        self.assertIsNotNone(resources)

        existing = coordinator.acquire(1, access)
        self.assertIsInstance(existing, AcquireExisting)

        mismatch = coordinator.acquire(1, other)
        self.assertIsInstance(mismatch, AcquireAccessMismatch)
        self.assertEqual(mismatch.current_access, access)

        released = coordinator.release(1, access)
        self.assertIsInstance(released, ReleaseDetached)
        self.assertEqual(released.next_generation, 2)
        self.assertIsNone(pool.lookup(access))
        self.assertEqual(pool.snapshot(), ())
        self.assertTrue(resources.cleaned)

    def test_release_revokes_preparing_and_late_result_is_cleaned(self) -> None:
        lifecycle = BlockingLifecycle()
        coordinator, pool = self.make_coordinator(lifecycle)
        access = Access("watch")
        result_holder: list[object] = []

        thread = threading.Thread(
            target=lambda: result_holder.append(coordinator.acquire(1, access)),
            daemon=True,
        )
        thread.start()
        self.assertTrue(lifecycle.started.wait(timeout=2))

        released = coordinator.release(1, access)
        self.assertIsInstance(released, ReleaseAcquisitionRevoked)
        self.assertEqual(released.next_generation, 2)

        lifecycle.allow_return.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(result_holder), 1)
        self.assertIsInstance(result_holder[0], AcquireSuperseded)
        self.assertTrue(lifecycle.saw_cancellation)
        self.assertEqual(len(lifecycle.cleaned), 1)
        self.assertEqual(pool.snapshot(), ())
        self.assertEqual(coordinator.read().generation, 2)

    def test_cleanup_failure_keeps_retired_record_and_blocks_reacquire(self) -> None:
        lifecycle = Lifecycle()
        coordinator, pool = self.make_coordinator(lifecycle)
        access = Access("server")

        committed = coordinator.acquire(1, access)
        self.assertIsInstance(committed, AcquireCommitted)
        resources = pool.lookup(access)
        self.assertIsNotNone(resources)

        lifecycle.fail_cleanup = True
        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            coordinator.release(1, access)

        self.assertEqual(coordinator.read().generation, 2)
        self.assertIsNone(pool.lookup(access))
        self.assertIs(pool.retired(access), resources)
        records = pool.snapshot()
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0].retired)

        blocked = coordinator.acquire(2, access)
        self.assertIsInstance(blocked, AcquireBusy)

        lifecycle.fail_cleanup = False
        self.assertTrue(coordinator.cleanup_retired(access))
        self.assertEqual(pool.snapshot(), ())

        recommitted = coordinator.acquire(2, access)
        self.assertIsInstance(recommitted, AcquireCommitted)

    def test_pool_reservation_is_identity_scoped(self) -> None:
        pool: ResourcePool[Access, ResourceSet] = ResourcePool()
        access = Access("server")
        first = pool.reserve(access)
        self.assertIsNotNone(first)
        self.assertIsNone(pool.reserve(access))
        self.assertTrue(pool.cancel(first))
        self.assertFalse(pool.cancel(first))
        self.assertIsNotNone(pool.reserve(access))

    def test_snapshot_cannot_mutate_pool_retirement_state(self) -> None:
        pool: ResourcePool[Access, ResourceSet] = ResourcePool()
        access = Access("server")
        reservation = pool.reserve(access)
        self.assertIsNotNone(reservation)
        resources = ResourceSet("server")
        pool.install(reservation, resources)

        record = pool.snapshot()[0]
        with self.assertRaises((AttributeError, TypeError)):
            record.retired = True
        self.assertIs(pool.lookup(access), resources)


if __name__ == "__main__":
    unittest.main()
