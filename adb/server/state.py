from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from _lifecycle_new.capability.snapshot import LifecyclePhase, LifecycleSnapshot
from adb.server.capability import AdbServerCapability
from adb.server.generation import AdbServerGeneration
from adb.server.request import AdbServerRequest


AdbServerPhase: TypeAlias = LifecyclePhase

AdbServerSnapshot: TypeAlias = LifecycleSnapshot[
    AdbServerGeneration,
    AdbServerRequest,
    AdbServerCapability,
]


@runtime_checkable
class AdbServerSnapshotReader(Protocol):
    """Read a consistent point-in-time ADB server lifecycle snapshot."""

    def read(self) -> AdbServerSnapshot:
        """Return the current generation, phase, and phase-specific fields."""
        ...


__all__ = [
    "AdbServerPhase",
    "AdbServerSnapshot",
    "AdbServerSnapshotReader",
]
