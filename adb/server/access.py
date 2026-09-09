from __future__ import annotations

from dataclasses import dataclass

from networking import TcpAddress


@dataclass(frozen=True, slots=True)
class AdbServerAccess:
    """Endpoint metadata published by an ADB server lifecycle.

    The enclosing state or snapshot pairs this value with its authority generation.
    """

    endpoint: TcpAddress

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


__all__ = ["AdbServerAccess"]
