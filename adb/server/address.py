"""Compatibility address values for the ADB server API."""

from networking import TcpAddress


class AdbServerTcpAddress(TcpAddress):
    """Compatibility TCP address retaining the historical ADB server defaults."""

    __slots__ = ()

    def __init__(self, host: str = "localhost", port: int = 5037) -> None:
        super().__init__(host, port)


__all__ = ["AdbServerTcpAddress"]
