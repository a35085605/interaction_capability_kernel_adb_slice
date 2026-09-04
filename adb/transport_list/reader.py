from __future__ import annotations

from typing import Protocol

from adb.transport_list.model import AdbTransportList
from networking import TcpAddress


class AdbTransportListReader(Protocol):
    """Read one complete current domain transport list."""

    def read(self, endpoint: TcpAddress) -> AdbTransportList:
        ...


__all__ = ["AdbTransportListReader"]
