from __future__ import annotations

from typing import Protocol

from adb.transport_list.model import AdbTransportList
from networking import TcpEndpoint


class AdbTransportListReader(Protocol):
    """Read one non-authoritative complete transport-list snapshot."""

    def read(self, server_endpoint: TcpEndpoint) -> AdbTransportList:
        ...


__all__ = ["AdbTransportListReader"]
