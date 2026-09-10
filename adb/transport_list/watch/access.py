from __future__ import annotations

from dataclasses import dataclass

from networking import TcpAddress


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchAccess:
    """Control-plane server address metadata published by a watch lifecycle.

    Lifecycle snapshots and acquire/release outcomes pair this value with the authority
    generation they describe.
    """

    server_address: TcpAddress

    def __post_init__(self) -> None:
        if not isinstance(self.server_address, TcpAddress):
            raise TypeError("server_address must be TcpAddress")


__all__ = ["AdbTransportListWatchAccess"]
