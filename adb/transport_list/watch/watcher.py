from __future__ import annotations

from typing import Protocol, runtime_checkable

from networking import TcpAddress
from adb.transport_list.watch.session import AdbTransportListWatchSession


@runtime_checkable
class AdbTransportListWatcher(Protocol):
    """Own one low-level transport-list watch attachment for an ADB server endpoint."""

    @property
    def address(self) -> TcpAddress:
        ...

    def open(self) -> AdbTransportListWatchSession | None:
        """Establish one watch session and synchronously obtain its initial complete list."""
        ...

    def close(self) -> None:
        """Release the watcher attachment and interrupt any active watch read."""
        ...


__all__ = ["AdbTransportListWatcher"]
