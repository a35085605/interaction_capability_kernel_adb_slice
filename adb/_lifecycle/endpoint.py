from __future__ import annotations

from dataclasses import dataclass

from networking import TcpAddress


@dataclass(frozen=True, slots=True)
class EndpointAccess:
    """Usable endpoint access information, independent of lifecycle authority generation."""

    endpoint: TcpAddress

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


__all__ = ["EndpointAccess"]
