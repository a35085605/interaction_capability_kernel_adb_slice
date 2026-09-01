from __future__ import annotations

from dataclasses import dataclass

from networking import TcpAddress
from adb.server.epoch import ServerEpoch


@dataclass(frozen=True, slots=True)
class AdbServerLifetime:
    """Immutable pairing of a runtime-scoped server epoch with its connection endpoint."""

    endpoint: TcpAddress
    epoch: ServerEpoch

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if not isinstance(self.epoch, ServerEpoch):
            raise TypeError("epoch must be ServerEpoch")


__all__ = ["AdbServerLifetime"]
