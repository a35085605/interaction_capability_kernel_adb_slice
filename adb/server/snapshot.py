from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from lifecycle.capability.lifecycle import LifecycleSnapshotReader
from lifecycle.capability.snapshot import LifecyclePhase, LifecycleSnapshot
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
class AdbServerSnapshotReader(
    LifecycleSnapshotReader[
        AdbServerGeneration,
        AdbServerRequest,
        AdbServerCapability,
    ],
    Protocol,
):
    """Read a consistent point-in-time ADB server lifecycle snapshot."""


__all__ = [
    "AdbServerPhase",
    "AdbServerSnapshot",
    "AdbServerSnapshotReader",
]
