from __future__ import annotations

from dataclasses import dataclass

from networking import TcpEndpoint


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchRequest:
    """Request one transport-list watch capability from an ADB server endpoint."""

    server_endpoint: TcpEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.server_endpoint, TcpEndpoint):
            raise TypeError("server_endpoint must be TcpEndpoint")


__all__ = ["AdbTransportListWatchRequest"]
