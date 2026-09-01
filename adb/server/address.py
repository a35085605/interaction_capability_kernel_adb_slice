"""ADB server address values."""

from networking import TcpAddress


class AdbServerTcpAddress(TcpAddress):
    """TCP address for an ADB server, defaulting to localhost:5037."""

    __slots__ = ()

    def __init__(self, host: str = "localhost", port: int = 5037) -> None:
        super().__init__(host, port)


__all__ = ["AdbServerTcpAddress"]
