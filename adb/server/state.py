from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress

from _lifecycle_new.capability.snapshot import LifecycleSnapshot
from adb._lifecycle import Snapshot
from adb.server.access import AdbServerAccess
from adb.server.generation import AdbServerGeneration


AdbServerState: TypeAlias = Snapshot[
    AdbServerGeneration,
    AdbServerAccess,
    TcpAddress,
]

# New synchronous lifecycle state. ``AdbServerState`` above remains the legacy
# committed-only snapshot until supervision and the existing adapters migrate.
AdbServerLifecycleSnapshot: TypeAlias = LifecycleSnapshot[
    AdbServerGeneration,
    AdbServerAccess,
    TcpAddress,
]


@runtime_checkable
class AdbServerStateView(Protocol):
    """Read a linearizable snapshot of current server authority and usable capability."""

    def read(self) -> AdbServerState:
        """Return one atomic generation/access/capability snapshot without leasing capability."""
        ...


__all__ = ["AdbServerLifecycleSnapshot", "AdbServerState", "AdbServerStateView"]
