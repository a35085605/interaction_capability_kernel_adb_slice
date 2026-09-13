from __future__ import annotations

from dataclasses import dataclass

from networking import TcpAddress


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchRequest:
    """Request one transport-list watch capability from an ADB server endpoint."""

    server_address: TcpAddress

    def __post_init__(self) -> None:
        if not isinstance(self.server_address, TcpAddress):
            raise TypeError("server_address must be TcpAddress")


__all__ = ["AdbTransportListWatchRequest"]
