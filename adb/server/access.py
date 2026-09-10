from __future__ import annotations

from dataclasses import dataclass

from networking import TcpAddress


@dataclass(frozen=True, slots=True)
class AdbServerAccess:
    """Server address metadata published by an ADB server lifecycle.

    The enclosing state or snapshot pairs this value with its authority generation.
    """

    server_address: TcpAddress

    def __post_init__(self) -> None:
        if not isinstance(self.server_address, TcpAddress):
            raise TypeError("server_address must be TcpAddress")


__all__ = ["AdbServerAccess"]
