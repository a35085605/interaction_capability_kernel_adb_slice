from __future__ import annotations

from dataclasses import dataclass

from networking import TcpEndpoint


@dataclass(frozen=True, slots=True)
class AdbServerRequest:
    """Request usable ADB server access at one TCP endpoint."""

    server_endpoint: TcpEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.server_endpoint, TcpEndpoint):
            raise TypeError("server_endpoint must be TcpEndpoint")


__all__ = ["AdbServerRequest"]
