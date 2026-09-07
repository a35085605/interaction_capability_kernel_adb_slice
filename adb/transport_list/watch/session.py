from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from adb.transport_list.model import AdbTransportList


@runtime_checkable
class AdbTransportListWatchSession(Protocol):
    """Established watch resources owned by one backend acquisition.

    The session is a physical resource handle only. Watch generation carries lifecycle and
    producer authority, matching the server backend's generation/handle separation.
    """

    @property
    def initial(self) -> AdbTransportList:
        ...

    def updates(self) -> Iterator[AdbTransportList]:
        ...

    def close(self) -> None:
        ...


__all__ = ["AdbTransportListWatchSession"]
