from __future__ import annotations

import unittest

from lifecycle.capability.coordinator import CapabilityLifecycleCoordinator
from lifecycle.capability.result import (
    AcquireFailed,
    AcquireSucceeded,
    ReleaseSucceeded,
)
from lifecycle.capability.snapshot import LifecyclePhase
from lifecycle.resource.result import ResourceAcquireFailed, ResourceAcquireSucceeded


class _GenerationIssuer:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self) -> int:
        self._value += 1
        return self._value


class _Provider:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.released: list[tuple[str, ...]] = []

    def acquire(self, request: str):
        resources = (f"resource:{request}",)
        if self.error is not None:
            return ResourceAcquireFailed(self.error, resources)
        return ResourceAcquireSucceeded(resources)

    def release(self, resources: tuple[str, ...]) -> None:
        self.released.append(resources)


class _Projector:
    def project(self, request: str, resources: tuple[str, ...]) -> str:
        return f"capability:{request}:{resources[0]}"


class CapabilityLifecycleCoordinatorTests(unittest.TestCase):
    def test_acquire_release_advances_generation(self) -> None:
        provider = _Provider()
        lifecycle = CapabilityLifecycleCoordinator(
            _GenerationIssuer(),
            provider,
            _Projector(),
        )
        initial = lifecycle.read()

        acquired = lifecycle.acquire(initial.generation, "device")
        self.assertIsInstance(acquired, AcquireSucceeded)
        self.assertEqual(acquired.snapshot.phase, LifecyclePhase.ACTIVE)

        released = lifecycle.release(initial.generation, "device")
        self.assertIsInstance(released, ReleaseSucceeded)
        self.assertNotEqual(released.next_generation, initial.generation)
        self.assertEqual(provider.released, [("resource:device",)])
        self.assertEqual(lifecycle.read().phase, LifecyclePhase.IDLE)

    def test_failed_acquire_retains_resources_until_release(self) -> None:
        provider = _Provider(error=RuntimeError("boom"))
        lifecycle = CapabilityLifecycleCoordinator(
            _GenerationIssuer(),
            provider,
            _Projector(),
        )
        initial = lifecycle.read()

        failed = lifecycle.acquire(initial.generation, "device")
        self.assertIsInstance(failed, AcquireFailed)
        self.assertEqual(failed.snapshot.phase, LifecyclePhase.RELEASE_REQUIRED)
        self.assertEqual(provider.released, [])

        released = lifecycle.release(initial.generation, "device")
        self.assertIsInstance(released, ReleaseSucceeded)
        self.assertEqual(provider.released, [("resource:device",)])


if __name__ == "__main__":
    unittest.main()
