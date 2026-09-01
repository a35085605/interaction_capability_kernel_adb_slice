"""ADB server endpoint values."""

from typing import TypeAlias

from networking import TcpAddress


# Domain-facing name for the TCP address used to connect to an ADB server.
AdbServerEndpoint: TypeAlias = TcpAddress


__all__ = ["AdbServerEndpoint"]
