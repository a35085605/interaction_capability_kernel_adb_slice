from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, TypeAlias, runtime_checkable

from networking import TcpAddress
from adb.tracking.observation import AdbTrackedTransportObservation


AdbTransportList: TypeAlias = tuple[AdbTrackedTransportObservation, ...]


@runtime_checkable
class AdbTransportListReader(Protocol):
    """Read one complete current transport list from an ADB server endpoint."""

    def read(self, address: TcpAddress) -> AdbTransportList:
        ...


@runtime_checkable
class AdbTransportListWatch(Protocol):
    """Established watch yielding complete domain transport lists."""

    @property
    def initial(self) -> AdbTransportList:
        ...

    def updates(self) -> Iterator[AdbTransportList]:
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class AdbTransportListWatcher(Protocol):
    """Own one low-level transport-list watch attachment for an ADB server endpoint."""

    @property
    def address(self) -> TcpAddress:
        ...

    def open(self) -> AdbTransportListWatch | None:
        """Establish one watch and synchronously obtain its initial complete list."""
        ...

    def close(self) -> None:
        """Release the watcher attachment and interrupt any active watch read."""
        ...


__all__ = [
    "AdbTransportList",
    "AdbTransportListReader",
    "AdbTransportListWatch",
    "AdbTransportListWatcher",
]
