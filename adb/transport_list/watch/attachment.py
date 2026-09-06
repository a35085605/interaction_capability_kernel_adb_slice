from __future__ import annotations

from typing import Protocol, runtime_checkable

from networking import TcpAddress
from adb.transport_list.watch.stream import AdbTransportListWatchStream


@runtime_checkable
class AdbTransportListWatchAttachment(Protocol):
    """One low-level, endpoint-bound attachment used by exactly one watch session."""

    @property
    def address(self) -> TcpAddress:
        ...

    def open(self) -> AdbTransportListWatchStream | None:
        """Open the attachment and synchronously obtain its initial complete list."""
        ...

    def close(self) -> None:
        """Release the attachment and interrupt any active open/read operation."""
        ...


__all__ = ["AdbTransportListWatchAttachment"]
