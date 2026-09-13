from __future__ import annotations

from dataclasses import dataclass

from networking import TcpEndpoint


@dataclass(frozen=True, slots=True)
class AdbServerCapability:
    """Describe usable access to the active ADB server."""

    server_address: TcpEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.server_address, TcpEndpoint):
            raise TypeError("server_address must be TcpEndpoint")


__all__ = ["AdbServerCapability"]
