from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from adb.transport_list.model import AdbTransportList


@runtime_checkable
class AdbTransportListWatchSession(Protocol):
    """Established watch session yielding complete domain transport lists."""

    @property
    def initial(self) -> AdbTransportList:
        ...

    def updates(self) -> Iterator[AdbTransportList]:
        ...

    def close(self) -> None:
        ...


__all__ = ["AdbTransportListWatchSession"]
