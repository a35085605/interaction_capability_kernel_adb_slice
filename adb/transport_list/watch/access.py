from __future__ import annotations

from dataclasses import dataclass

from networking import TcpAddress


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchAccess:
    """Endpoint metadata published by a transport-list watch lifecycle.

    The enclosing state or snapshot pairs this value with its authority generation.
    """

    endpoint: TcpAddress

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


__all__ = ["AdbTransportListWatchAccess"]
