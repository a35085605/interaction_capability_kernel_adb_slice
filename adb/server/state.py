from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress

from adb._lifecycle import Snapshot
from adb.server.access import AdbServerAccess
from adb.server.generation import AdbServerGeneration


AdbServerState: TypeAlias = Snapshot[
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


__all__ = ["AdbServerState", "AdbServerStateView"]
